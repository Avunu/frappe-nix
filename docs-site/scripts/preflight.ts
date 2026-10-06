// Checks, before anything is built, that the site is set up and that the installed Jx packages are
// the releases the documentation depends on. `bun run build` and `bun run dev` run it.
//
//   bun scripts/preflight.ts
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { readSnapshot } from "./lib/catalog.ts";
import { ROOT, docsDir, isPlaceholder, readConfig, validateConfig } from "./lib/config.ts";

export interface Report {
  errors: string[];
  warnings: string[];
}

const flat = (slug: string) => slug.replaceAll("_", "-");

/**
 * What the committed catalog snapshot says about the configured slug: an error when a different
 * spelling of it is the catalog's key (`erpnext-taskview` for `erpnext_taskview`: the switcher would
 * never mark the project), a warning when it is not in the catalog at all, nothing when it is there.
 */
export function checkSlug(root: string, slug: string): { error?: string; warning?: string } {
  const slugs = readSnapshot(root).map((p) => p.slug);
  if (slugs.length === 0 || slugs.includes(slug)) return {};
  const close = slugs.find((candidate) => flat(candidate) === flat(slug));
  if (close) {
    return {
      error: `docs.config.json: slug "${slug}" is not in the project catalog, but "${close}" is: the switcher would never mark this project as the current one. Set "slug" to "${close}" (the domain stays as it is).`,
    };
  }
  return {
    warning: `slug "${slug}" is not in data/projects.snapshot.json, so the project switcher will not mark this project as the current one. Add the project to the avunu.net catalog first, then run bun run sync:projects.`,
  };
}

/**
 * The released Jx packages this site is written against: the oldest version of each that works.
 * `package.json` asks for the same ranges; this catches a `bun.lock` or `node_modules` left behind
 * by an older checkout (a parser 1.x does not read GitHub alerts, `exclude`/`where`/`route` or
 * relative links, and a compiler 4.x cannot run a parser 2.x).
 */
export const MINIMUM_JX: Record<string, string> = {
  parser: "2.0.0",
  compiler: "5.0.0",
  search: "0.4.0",
  runtime: "4.0.3",
};

const parts = (version: string): number[] =>
  version
    .split("-")[0]!
    .split(".")
    .map((part) => Number.parseInt(part, 10) || 0);

/** Whether `version` is `minimum` or newer (numeric, a pre-release suffix is ignored). */
export function atLeast(version: string, minimum: string): boolean {
  const have = parts(version);
  const need = parts(minimum);
  for (let i = 0; i < Math.max(have.length, need.length); i++) {
    const a = have[i] ?? 0;
    const b = need[i] ?? 0;
    if (a !== b) return a > b;
  }
  return true;
}

export function checkJx(root: string = ROOT): string | null {
  const problems: string[] = [];
  for (const [name, minimum] of Object.entries(MINIMUM_JX)) {
    const manifest = join(root, "node_modules", "@jxsuite", name, "package.json");
    if (!existsSync(manifest)) {
      problems.push(`@jxsuite/${name} is not installed. Run bun install.`);
      continue;
    }
    let version = "unknown";
    try {
      version =
        (JSON.parse(readFileSync(manifest, "utf8")) as { version?: string }).version ?? version;
    } catch {
      // an unreadable manifest is reported as an unknown version below
    }
    if (version === "unknown" || !atLeast(version, minimum)) {
      problems.push(
        `The installed @jxsuite/${name} (${version}) is older than this site needs (${minimum} or newer).`,
      );
    }
  }
  if (problems.length === 0) return null;
  const outdated = problems.some((problem) => problem.includes("is older"));
  return (
    problems.join(" ") +
    (outdated
      ? ' Update them and commit bun.lock: bun update @jxsuite/compiler @jxsuite/parser @jxsuite/runtime @jxsuite/search @jxsuite/server. See the README ("Jx version").'
      : "")
  );
}

export function preflight(root: string = ROOT, options: { jx?: boolean } = {}): Report {
  const report: Report = { errors: [], warnings: [] };
  let config;
  try {
    config = readConfig(root);
  } catch (error) {
    report.errors.push((error as Error).message);
    return report;
  }
  if (isPlaceholder(config)) {
    report.errors.push(
      'docs.config.json still has the template values. Run: bun scripts/init.ts --name "<Project>" ...',
    );
  }
  for (const problem of validateConfig(config)) report.errors.push(`docs.config.json: ${problem}`);

  const project = JSON.parse(readFileSync(join(root, "project.json"), "utf8")) as {
    name?: string;
    url?: string;
  };
  if (project.name !== config.name || project.url !== `https://${config.domain}`) {
    report.errors.push(
      `project.json (name "${project.name}", url "${project.url}") does not match docs.config.json (name "${config.name}", domain "${config.domain}"). Run init again, or edit one to match the other.`,
    );
  }
  const cname = join(root, "public", "CNAME");
  if (existsSync(cname) && readFileSync(cname, "utf8").trim() !== config.domain) {
    report.errors.push(`public/CNAME does not say ${config.domain}`);
  }
  const docs = docsDir(root);
  if (!existsSync(docs)) {
    report.errors.push(
      "The documentation folder ../docs does not exist. Put your Markdown in docs/ at the repository root; docs/README.md is the home page.",
    );
  } else if (!readdirSync(docs).some((name) => /^(?:readme|index)\.md$/i.test(name))) {
    report.errors.push(
      "docs/README.md is missing: it is the documentation home (/docs/). Add one (docs/index.md works too); for a repository whose README is its documentation: cp README.md docs/README.md",
    );
  }
  const slug = config.slug ? checkSlug(root, config.slug) : {};
  if (slug.error) report.errors.push(slug.error);
  if (slug.warning) report.warnings.push(slug.warning);
  if (options.jx !== false) {
    const jx = checkJx(root);
    if (jx) report.errors.push(jx);
  }
  return report;
}

if (import.meta.main) {
  const report = preflight();
  for (const warning of report.warnings) console.warn(`preflight: warning: ${warning}`);
  if (report.errors.length > 0) {
    for (const error of report.errors) console.error(`preflight: ${error}`);
    process.exit(1);
  }
  console.log("preflight: OK");
}
