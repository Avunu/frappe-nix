// Fills in this site for one project. Run it once after copying the template into a repository:
//
//   cd docs-site && bun scripts/init.ts
//
// With no options it reads everything it can from the repository (the `origin` remote, its default
// branch) and from the committed project catalog (data/projects.snapshot.json: the entry that has
// this repository's URL gives the slug, name, tagline, platform and license). Say what it cannot
// find, or what it should do differently:
//
//   bun scripts/init.ts --name "frappe-nix" --tagline "Reusable Nix infrastructure for Frappe benches." \
//     --platform nixos --license MIT --repo https://github.com/Avunu/frappe-nix
//
// It writes docs.config.json and public/CNAME, sets the name and address in project.json and the
// package name, and moves the GitHub Pages workflow (with the repository's default branch written
// into it) and the Dependabot configuration to the repository's .github folder. Run again, it keeps
// what docs.config.json already says: `bun scripts/init.ts --force` re-applies it after the template
// has been updated.
//
// Options (every one has a default):
//   --name       display name (default: the catalog entry's title, else required)
//   --tagline    one sentence under the name on the landing page and in search results (default: the catalog's summary)
//   --slug       the project's slug in avunu.net/projects.json (default: the catalog entry for the repository, else the repository's name)
//   --platform   frappe | odoo | wordpress | nixos | general (default: the catalog's, else general)
//   --repo       GitHub URL (default: this repository's origin remote)
//   --domain     the custom domain (default: the slug with hyphens for underscores, then .avunu.net)
//   --license    SPDX identifier (default: the catalog's, else required)
//   --branch     the repository's default branch; the workflow publishes from it and "Edit this page" opens it (default: origin/HEAD, else main)
//   --repo-root  the repository root (default: the git top level, else the parent folder)
//   --no-workflow  leave the workflow and the Dependabot configuration where they are
//   --force      replace a workflow that already exists at the repository root
//   --dry-run    print what would change and write nothing
import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  rmdirSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { parseArgs } from "node:util";
import { entryFor, readSnapshot, repoName } from "./lib/catalog.ts";
import {
  BRANCH,
  type DocsConfig,
  PLATFORMS,
  ROOT,
  isPlaceholder,
  validateConfig,
} from "./lib/config.ts";

export interface InitOptions {
  name?: string;
  tagline?: string;
  slug?: string;
  platform?: string;
  repo?: string;
  domain?: string;
  license?: string;
  branch?: string;
  repoRoot?: string;
  workflow?: boolean;
  force?: boolean;
  dryRun?: boolean;
}

/** `git@github.com:Avunu/x.git`, `ssh://git@github.com/Avunu/x` and `https://…/x.git` all become https://github.com/Avunu/x. */
export function normalizeRemote(remote: string): string | null {
  const trimmed = remote.trim().replace(/\.git$/, "");
  const ssh = /^(?:ssh:\/\/)?git@github\.com[:/]([\w.-]+\/[\w.-]+)$/.exec(trimmed);
  if (ssh) return `https://github.com/${ssh[1]}`;
  const https = /^https:\/\/(?:[^@/]+@)?github\.com\/([\w.-]+\/[\w.-]+)$/.exec(trimmed);
  return https ? `https://github.com/${https[1]}` : null;
}

function git(args: string[], cwd: string): string | null {
  try {
    const result = Bun.spawnSync(["git", ...args], { cwd, stdout: "pipe", stderr: "pipe" });
    return result.exitCode === 0 ? result.stdout.toString().trim() : null;
  } catch {
    return null;
  }
}

/** The repository's default branch as the clone knows it (`origin/HEAD`), or null. */
export function defaultBranch(cwd: string): string | null {
  const head = git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd);
  const name = head?.replace(/^origin\//, "") ?? "";
  return name !== "" && BRANCH.test(name) ? name : null;
}

/** The slug a repository gets when the catalog does not know it: its name, as the catalog writes keys. */
const slugFromName = (text: string) =>
  text
    .toLowerCase()
    .replaceAll(/[^a-z0-9_]+/g, "-")
    .replaceAll(/^[-_]+|[-_]+$/g, "");

/** The first line of the repository's licence file, as a hint for the person who has to name the licence. */
function licenseFile(repoRoot: string): { file: string; first: string } | null {
  for (const file of [
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "LICENCE",
    "COPYING",
    "license.txt",
  ]) {
    try {
      const first = readFileSync(join(repoRoot, file), "utf8")
        .split(/\r?\n/)
        .find((line) => line.trim() !== "");
      if (first) return { file, first: first.trim().slice(0, 60) };
    } catch {
      // not there: try the next name
    }
  }
  return null;
}

/** Everything init decided, and where each value came from (for the summary). */
export interface Decision {
  config: DocsConfig;
  /** One line per value that was not given as an option. */
  notes: string[];
}

function existingConfig(siteDir: string): Partial<DocsConfig> {
  try {
    const found = JSON.parse(
      readFileSync(join(siteDir, "docs.config.json"), "utf8"),
    ) as Partial<DocsConfig>;
    return isPlaceholder(found) ? {} : found;
  } catch {
    return {};
  }
}

/** The configuration for the options, with defaults filled in. Throws a list of problems. */
export function decide(options: InitOptions, siteDir: string): Decision {
  const notes: string[] = [];
  const have = existingConfig(siteDir);
  const origin = git(["remote", "get-url", "origin"], siteDir);

  const repoGiven = options.repo?.trim() || have.repo || (origin ? normalizeRemote(origin) : null);
  const projects = readSnapshot(siteDir);
  const lookup = repoGiven ? entryFor(projects, repoGiven) : { ambiguous: [] };
  const named = options.slug?.trim()
    ? projects.find((p) => p.slug === options.slug!.trim())
    : undefined;
  if (!options.slug && !have.slug && !lookup.entry && lookup.ambiguous.length > 1) {
    throw new Error(
      `${repoGiven} has ${lookup.ambiguous.length} catalog entries (${lookup.ambiguous.map((p) => p.slug).join(", ")}): pass --slug with the one this site is for`,
    );
  }
  const entry = named ?? (options.slug ? undefined : lookup.entry);

  const slug =
    options.slug?.trim() ||
    have.slug ||
    entry?.slug ||
    slugFromName(repoGiven ? repoName(repoGiven) : (options.name ?? ""));
  if (!options.slug && !have.slug) {
    notes.push(
      entry
        ? `slug ${slug}: the catalog entry for ${repoGiven}`
        : `slug ${slug}: the repository's name (it is not in the catalog snapshot)`,
    );
  }

  const name = options.name?.trim() || have.name || entry?.title;
  if (!name) throw new Error('--name is required, for example --name "frappe-nix"');
  if (!options.name && !have.name) notes.push(`name ${name}: the catalog's title`);

  const tagline = options.tagline?.trim() || have.tagline || entry?.summary?.trim();
  if (!tagline) {
    throw new Error(
      '--tagline is required: one sentence that says what the project does, for example --tagline "Reusable Nix infrastructure for Frappe benches."',
    );
  }
  if (!options.tagline && !have.tagline) notes.push("tagline: the catalog's summary");

  const license = options.license?.trim() || have.license || entry?.license?.trim();
  if (!license) {
    const found = licenseFile(dirname(siteDir));
    throw new Error(
      `--license is required: an SPDX identifier, for example --license MIT${entry ? ` (the catalog entry ${entry.slug} has none; add it there too)` : ""}${found ? `. The repository's ${found.file} starts with "${found.first}"` : ""}`,
    );
  }
  if (!options.license && !have.license) notes.push(`license ${license}: the catalog's`);

  const platform = (options.platform?.trim() ||
    have.platform ||
    entry?.platform ||
    "general") as DocsConfig["platform"];
  if (!options.platform && !have.platform)
    notes.push(`platform ${platform}: ${entry ? "the catalog's" : "the default; pass --platform"}`);

  const repo = repoGiven || `https://github.com/Avunu/${slug}`;
  if (!options.repo && !have.repo)
    notes.push(`repo ${repo}: ${origin ? "the origin remote" : "a guess; pass --repo"}`);

  const domain =
    options.domain?.trim() ||
    (have.domain && (!options.slug || options.slug === have.slug) ? have.domain : undefined) ||
    `${slug.replaceAll("_", "-")}.avunu.net`;

  const found = options.branch?.trim() || have.branch || defaultBranch(siteDir);
  const branch = found || "main";
  if (!options.branch && !have.branch)
    notes.push(
      found
        ? `branch ${branch}: the repository's default branch (origin/HEAD)`
        : "branch main: origin/HEAD is not set in this clone, so the default branch is assumed; pass --branch if it is not main",
    );

  const config: DocsConfig = { name, tagline, slug, platform, repo, domain, license };
  if (branch !== "main") config.branch = branch;
  const problems = validateConfig(config);
  if (problems.length > 0)
    throw new Error(`The options are not valid:\n  - ${problems.join("\n  - ")}`);
  return { config, notes };
}

/** The configuration for the options (see `decide` for where each value came from). */
export function configFrom(options: InitOptions, siteDir: string): DocsConfig {
  return decide(options, siteDir).config;
}

function writeJson(file: string, value: unknown, dry: boolean): void {
  if (!dry) writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`);
}

/** The workflow for a site folder and a branch: the folder name and the trigger branch filled in. */
export function adaptWorkflow(template: string, dir: string, branch: string): string {
  return template
    .replaceAll("docs-site", dir)
    .replace(/branches: \[[^\]]*\]/, `branches: [${JSON.stringify(branch)}]`);
}

/** The Dependabot configuration for a site folder. */
export function adaptDependabot(template: string, dir: string): string {
  return template.replaceAll("/docs-site", dir === "." ? "/" : `/${dir}`);
}

/**
 * Writes `branch` into the trigger of an existing workflow (`branches: ["old"]`). Leaves a trigger
 * with several branches or none alone. Returns the branch it replaced, or null when nothing changed.
 */
export function syncBranch(file: string, branch: string, dry: boolean): string | null {
  const text = readFileSync(file, "utf8");
  const match = /branches: \[\s*"?([^\]",\s]+)"?\s*\]/.exec(text);
  if (!match || match[1] === branch) return null;
  if (!dry) writeFileSync(file, text.replace(match[0], `branches: [${JSON.stringify(branch)}]`));
  return match[1]!;
}

/** Moves a file of the site's .github folder to the repository's, then prunes empty folders. */
function relocate(
  from: string,
  to: string,
  render: (text: string) => string,
  force: boolean,
  dry: boolean,
  label: string,
  repoRoot: string,
  changes: string[],
  whenKept: string,
): void {
  if (!existsSync(from)) return;
  if (existsSync(to) && !force) {
    changes.push(
      `${label} already exists at ${to.replace(`${repoRoot}/`, "")}: left alone (${whenKept})`,
    );
    return;
  }
  if (!dry) {
    mkdirSync(dirname(to), { recursive: true });
    writeFileSync(to, render(readFileSync(from, "utf8")));
    // The template's copy goes, so the site folder never holds a file GitHub would ignore.
    rmSync(from);
    for (const folder of [dirname(from), dirname(dirname(from))]) {
      if (existsSync(folder) && readdirSync(folder).length === 0) rmdirSync(folder);
    }
  }
  changes.push(`${label} -> ${to.replace(`${repoRoot}/`, "")}`);
}

/** Applies the configuration. Returns a line per change, for the summary. */
export function applyInit(
  siteDir: string,
  config: DocsConfig,
  options: InitOptions = {},
): string[] {
  const dry = options.dryRun === true;
  const changes: string[] = [];

  writeJson(join(siteDir, "docs.config.json"), config, dry);
  changes.push("docs.config.json");

  const projectFile = join(siteDir, "project.json");
  const project = JSON.parse(readFileSync(projectFile, "utf8")) as Record<string, unknown>;
  project.name = config.name;
  project.url = `https://${config.domain}`;
  writeJson(projectFile, project, dry);
  changes.push("project.json (name, url)");

  const packageFile = join(siteDir, "package.json");
  if (existsSync(packageFile)) {
    const pkg = JSON.parse(readFileSync(packageFile, "utf8")) as Record<string, unknown>;
    pkg.name = `${config.slug.replaceAll("_", "-")}-docs`;
    writeJson(packageFile, pkg, dry);
    changes.push("package.json (name)");
  }

  const cname = join(siteDir, "public", "CNAME");
  if (!dry) {
    mkdirSync(dirname(cname), { recursive: true });
    writeFileSync(cname, `${config.domain}\n`);
  }
  changes.push("public/CNAME");

  if (options.workflow !== false) {
    const repoRoot = resolve(
      options.repoRoot ?? git(["rev-parse", "--show-toplevel"], siteDir) ?? dirname(siteDir),
    );
    const dir = siteDir === repoRoot ? "." : siteDir.slice(repoRoot.length + 1);
    relocate(
      join(siteDir, ".github", "workflows", "docs.yml"),
      join(repoRoot, ".github", "workflows", "docs.yml"),
      (text) => adaptWorkflow(text, dir, config.branch ?? "main"),
      options.force === true,
      dry,
      `.github/workflows/docs.yml (site folder ${dir}, publishes from ${config.branch ?? "main"})`,
      repoRoot,
      changes,
      "use --force to replace it",
    );
    // The template's workflow is gone from the site folder once it has been moved: a changed branch
    // is then written into the repository's own copy, if that has the one-branch trigger init wrote.
    const moved = join(repoRoot, ".github", "workflows", "docs.yml");
    if (!existsSync(join(siteDir, ".github", "workflows", "docs.yml")) && existsSync(moved)) {
      const branch = config.branch ?? "main";
      const was = syncBranch(moved, branch, dry);
      if (was) changes.push(`.github/workflows/docs.yml: publishes from ${branch} (was ${was})`);
    }
    relocate(
      join(siteDir, ".github", "dependabot.yml"),
      join(repoRoot, ".github", "dependabot.yml"),
      (text) => adaptDependabot(text, dir),
      false,
      dry,
      ".github/dependabot.yml",
      repoRoot,
      changes,
      `add the github-actions and bun entries of ${dir}/.github/dependabot.yml to it, then delete that file`,
    );
  }
  return changes;
}

if (import.meta.main) {
  const { values } = parseArgs({
    args: process.argv.slice(2),
    options: {
      name: { type: "string" },
      tagline: { type: "string" },
      slug: { type: "string" },
      platform: { type: "string" },
      repo: { type: "string" },
      domain: { type: "string" },
      license: { type: "string" },
      branch: { type: "string" },
      "repo-root": { type: "string" },
      "no-workflow": { type: "boolean" },
      force: { type: "boolean" },
      "dry-run": { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });
  if (values.help) {
    console.log(
      `Usage: bun scripts/init.ts [--name "<Project>"] [--tagline ...] [--platform ${PLATFORMS.join("|")}] [--repo URL] [--license SPDX] [--slug ...] [--branch ...]\nWith no options it uses the origin remote and the project catalog. See the comments at the top of scripts/init.ts for every option.`,
    );
    process.exit(0);
  }
  const options: InitOptions = {
    name: values.name,
    tagline: values.tagline,
    slug: values.slug,
    platform: values.platform,
    repo: values.repo,
    domain: values.domain,
    license: values.license,
    branch: values.branch,
    repoRoot: values["repo-root"],
    workflow: values["no-workflow"] ? false : true,
    force: values.force,
    dryRun: values["dry-run"],
  };
  try {
    const { config, notes } = decide(options, ROOT);
    const changes = applyInit(ROOT, config, options);
    console.log(`${options.dryRun ? "init (dry run): would write" : "init: wrote"}`);
    for (const change of changes) console.log(`  ${change}`);
    if (notes.length > 0) {
      console.log("\nchosen for you (pass the option to change it):");
      for (const note of notes) console.log(`  ${note}`);
    }
    const label = config.domain.split(".")[0];
    console.log(`\n${basename(ROOT)} is set up for ${config.name}.`);
    console.log("To publish it, a maintainer sets (see the README):");
    console.log(`  GitHub Pages custom domain  ${config.domain}`);
    console.log(`  Cloudflare DNS              CNAME  ${label}  ->  avunu.github.io   (DNS only)`);
    console.log(`  avunu.net catalog entry     docs: https://${config.domain}`);
    console.log(`  publishes from             ${config.branch ?? "main"} (the default branch)`);
    console.log("\nNext: bun install, then bun run build (see the README).");
  } catch (error) {
    console.error(`init: ${(error as Error).message}`);
    process.exit(1);
  }
}
