import { afterAll, expect, test } from "bun:test";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  adaptDependabot,
  adaptWorkflow,
  applyInit,
  configFrom,
  decide,
  defaultBranch,
  normalizeRemote,
} from "./init.ts";
import { ROOT } from "./lib/config.ts";
import { cleanup, makeTree, workflowLocation } from "./lib/test-utils.ts";

afterAll(cleanup);

const entry = (slug: string, repo: string, extra: Record<string, unknown> = {}) => ({
  slug,
  title: slug.toUpperCase(),
  platform: "frappe",
  summary: `${slug} does a thing.`,
  repo: `https://github.com/Avunu/${repo}`,
  page: `https://avunu.net/open-source/${slug}/`,
  docs: null,
  license: "MIT",
  status: "active",
  suite: null,
  ...extra,
});
const snapshot = JSON.stringify({
  version: 1,
  generated: "2026-10-05T00:00:00Z",
  site: "https://avunu.net",
  projects: [
    entry("erpnext_taskview", "erpnext_taskview", { title: "ERPNext TaskView" }),
    entry("avunu-odoo-addons", "avunu-odoo-addons", { platform: "odoo" }),
    entry("odoo-ai", "avunu-odoo-addons", { platform: "odoo" }),
    entry("odoo-ui", "avunu-odoo-addons", { platform: "odoo" }),
    entry("one", "two-entries"),
    entry("two", "two-entries"),
    entry("no-license", "no-license", { license: null }),
  ],
});

// The real workflow and Dependabot file exist only in the template: init moves them out of the site
// folder, so in an adopting repository these tests run on small stand-ins and the ones about the real
// files are skipped.
const location = workflowLocation();
const real = location?.template === true;
const STAND_IN_WORKFLOW = `name: Docs
on:
  push:
    branches: ["main"]
jobs:
  deploy:
    if: \${{ github.ref == format('refs/heads/{0}', github.event.repository.default_branch) }}
    defaults:
      run:
        working-directory: docs-site
    steps:
      - run: echo
        with:
          path: docs-site/dist
`;
const TEMPLATE_WORKFLOW = real
  ? readFileSync(join(ROOT, ".github", "workflows", "docs.yml"), "utf8")
  : STAND_IN_WORKFLOW;
const TEMPLATE_DEPENDABOT = real
  ? readFileSync(join(ROOT, ".github", "dependabot.yml"), "utf8")
  : "version: 2\nupdates:\n  - package-ecosystem: bun\n    directory: /docs-site\n";
const realOnly = real ? test : test.skip;

/** A repository with a docs-site folder that looks like a fresh copy of the template. */
function site(extra: Record<string, string> = {}, workflow = TEMPLATE_WORKFLOW) {
  const repo = makeTree({
    "docs-site/project.json": JSON.stringify({
      name: "Project Name",
      url: "https://project-name.avunu.net",
      other: 1,
    }),
    "docs-site/package.json": JSON.stringify({ name: "project-name-docs", private: true }),
    "docs-site/docs.config.json": JSON.stringify({
      name: "Project Name",
      tagline: "One sentence that says what this project does and who it is for.",
      slug: "project-name",
    }),
    "docs-site/data/projects.snapshot.json": snapshot,
    "docs-site/.github/workflows/docs.yml": workflow,
    "docs-site/.github/dependabot.yml": TEMPLATE_DEPENDABOT,
    ...extra,
  });
  return { repo, dir: join(repo, "docs-site") };
}

const base = {
  name: "x",
  tagline: "x does a thing.",
  license: "MIT",
  repo: "https://github.com/Avunu/x",
};

/** A git repository whose origin/HEAD says `branch`, as a clone of that repository would. */
function clone(branch: string | null) {
  const repo = makeTree({ "docs-site/project.json": "{}" });
  const git = (...args: string[]) => Bun.spawnSync(["git", ...args], { cwd: repo });
  git("init", "-q", "-b", "feature");
  git("remote", "add", "origin", "https://github.com/Avunu/x.git");
  if (branch) git("symbolic-ref", "refs/remotes/origin/HEAD", `refs/remotes/origin/${branch}`);
  return { repo, dir: join(repo, "docs-site") };
}

test("remotes of every shape become an https GitHub URL", () => {
  expect(normalizeRemote("git@github.com:Avunu/frappe-nix.git")).toBe(
    "https://github.com/Avunu/frappe-nix",
  );
  expect(normalizeRemote("ssh://git@github.com/Avunu/frappe-nix")).toBe(
    "https://github.com/Avunu/frappe-nix",
  );
  expect(normalizeRemote("https://github.com/Avunu/frappe-nix.git")).toBe(
    "https://github.com/Avunu/frappe-nix",
  );
  expect(normalizeRemote("https://x-access-token:abc@github.com/Avunu/frappe-nix")).toBe(
    "https://github.com/Avunu/frappe-nix",
  );
  expect(normalizeRemote("https://gitlab.com/Avunu/x")).toBeNull();
});

test("the slug defaults to the repository's name, which keeps its underscore; the domain does not", () => {
  const { dir } = site();
  const config = configFrom(
    { name: "TaskView", tagline: "T.", license: "MIT", repo: "https://github.com/Avunu/some_app" },
    dir,
  );
  expect(config).toMatchObject({
    slug: "some_app",
    domain: "some-app.avunu.net",
    platform: "general",
  });
  expect(
    configFrom(
      {
        name: "Cloudflare Email Relay",
        tagline: "T.",
        license: "MIT",
        slug: "cloudflare-email-relay",
        domain: "cloudflare-email.avunu.net",
        repo: "https://github.com/Avunu/cloudflare-email-relay",
      },
      dir,
    ),
  ).toMatchObject({ slug: "cloudflare-email-relay", domain: "cloudflare-email.avunu.net" });
  // With no repository to name it, the slug comes from the name.
  expect(
    configFrom({ name: "My Cool Project!", tagline: "T.", license: "MIT", repo: "" }, dir).slug,
  ).toBe("my-cool-project");
});

test("a repository the catalog lists gets its slug, name, tagline, platform and license from it", () => {
  const { dir } = site();
  const { config, notes } = decide({ repo: "https://github.com/Avunu/erpnext_taskview" }, dir);
  expect(config).toMatchObject({
    name: "ERPNext TaskView",
    slug: "erpnext_taskview",
    tagline: "erpnext_taskview does a thing.",
    platform: "frappe",
    license: "MIT",
    domain: "erpnext-taskview.avunu.net",
  });
  expect(notes.some((n) => n.includes("catalog"))).toBe(true);
  // What was given wins over the catalog.
  expect(
    decide(
      { repo: "https://github.com/Avunu/erpnext_taskview", name: "Mine", platform: "odoo" },
      dir,
    ).config,
  ).toMatchObject({ name: "Mine", platform: "odoo", slug: "erpnext_taskview" });
});

test("a repository with several catalog entries uses the one named like it, else asks for --slug", () => {
  const { dir } = site();
  expect(decide({ repo: "https://github.com/Avunu/avunu-odoo-addons" }, dir).config.slug).toBe(
    "avunu-odoo-addons",
  );
  expect(() => decide({ repo: "https://github.com/Avunu/two-entries" }, dir)).toThrow(
    /one, two.*--slug/,
  );
  expect(
    decide({ repo: "https://github.com/Avunu/two-entries", slug: "two" }, dir).config,
  ).toMatchObject({ slug: "two", name: "TWO" });
});

test("what the catalog cannot say is asked for, and nothing is guessed about a license", () => {
  const { dir } = site();
  expect(() => decide({ repo: "https://github.com/Avunu/unknown" }, dir)).toThrow(
    /--name is required/,
  );
  expect(() => decide({ name: "x", repo: "https://github.com/Avunu/unknown" }, dir)).toThrow(
    /--tagline is required/,
  );
  expect(() =>
    decide({ name: "x", tagline: "T.", repo: "https://github.com/Avunu/unknown" }, dir),
  ).toThrow(/--license is required/);
  expect(() => decide({ repo: "https://github.com/Avunu/no-license" }, dir)).toThrow(
    /--license is required.*catalog entry no-license has none/,
  );
});

test("a bad value is an error that says what to change", () => {
  const { dir } = site();
  expect(() => configFrom({ ...base, platform: "erpnext" }, dir)).toThrow(/platform/);
  expect(() => configFrom({ ...base, domain: "x.avunu.net/" }, dir)).toThrow(/domain/);
  expect(() => configFrom({ ...base, branch: "main; rm -rf /" }, dir)).toThrow(/branch/);
  expect(() => configFrom({ ...base, branch: 'a"b' }, dir)).toThrow(/branch/);
});

test("the default branch is read from origin/HEAD and kept only when it is not main", () => {
  const develop = clone("develop");
  expect(defaultBranch(develop.dir)).toBe("develop");
  expect(configFrom(base, develop.dir)).toMatchObject({ branch: "develop" });
  const version = clone("18.0");
  expect(configFrom(base, version.dir)).toMatchObject({ branch: "18.0" });
  const main = clone("main");
  expect(configFrom(base, main.dir)).not.toHaveProperty("branch");
  // No origin/HEAD: main is assumed, and the decision says so.
  const unknown = clone(null);
  expect(defaultBranch(unknown.dir)).toBeNull();
  expect(decide(base, unknown.dir).notes.some((n) => n.includes("origin/HEAD is not set"))).toBe(
    true,
  );
  // An explicit --branch wins.
  expect(configFrom({ ...base, branch: "trunk" }, develop.dir)).toMatchObject({ branch: "trunk" });
});

test("init writes the configuration, the address, the CNAME and the package name", () => {
  const { repo, dir } = site();
  const config = configFrom(
    {
      name: "frappe-nix",
      tagline: "Nix for Frappe.",
      license: "MIT",
      platform: "nixos",
      repo: "https://github.com/Avunu/frappe-nix",
    },
    dir,
  );
  applyInit(dir, config, { repoRoot: repo });
  expect(JSON.parse(readFileSync(join(dir, "docs.config.json"), "utf8"))).toEqual(config);
  expect(JSON.parse(readFileSync(join(dir, "project.json"), "utf8"))).toEqual({
    name: "frappe-nix",
    url: "https://frappe-nix.avunu.net",
    other: 1,
  });
  expect(JSON.parse(readFileSync(join(dir, "package.json"), "utf8")).name).toBe("frappe-nix-docs");
  expect(readFileSync(join(dir, "public", "CNAME"), "utf8")).toBe("frappe-nix.avunu.net\n");
});

realOnly(
  "the real workflow publishes from the repository's default branch, whatever it is called",
  () => {
    for (const branch of ["main", "develop", "18.0", "release/1.x"]) {
      const { repo, dir } = site();
      applyInit(dir, configFrom({ ...base, branch }, dir), { repoRoot: repo });
      const text = readFileSync(join(repo, ".github", "workflows", "docs.yml"), "utf8");
      expect(text).toContain(`branches: [${JSON.stringify(branch)}]`);
      // The deploy job compares with the repository's default branch, never a literal.
      expect(text).toContain("format('refs/heads/{0}', github.event.repository.default_branch)");
      expect(text).not.toMatch(/refs\/heads\/main/);
      expect(text).toContain("working-directory: docs-site");
      expect(text.match(/branches: \[/g)).toHaveLength(1);
    }
  },
);

test("the workflow learns the folder name, and only the folder name and the branch change", () => {
  const out = adaptWorkflow(TEMPLATE_WORKFLOW, "website", "main");
  expect(out).toContain("working-directory: website");
  expect(out).toContain("path: website/dist");
  expect(out).not.toContain("docs-site");
  expect(adaptWorkflow(TEMPLATE_WORKFLOW, "docs-site", "main")).toBe(TEMPLATE_WORKFLOW);
  expect(adaptDependabot(TEMPLATE_DEPENDABOT, "website")).toContain("directory: /website");
});

test("the workflow and the Dependabot configuration move to the repository root", () => {
  const { repo, dir } = site();
  applyInit(dir, configFrom({ ...base, branch: "develop" }, dir), { repoRoot: repo });
  expect(existsSync(join(repo, ".github", "workflows", "docs.yml"))).toBe(true);
  expect(readFileSync(join(repo, ".github", "dependabot.yml"), "utf8")).toContain(
    "directory: /docs-site",
  );
  expect(existsSync(join(dir, ".github"))).toBe(false);
});

test("an existing workflow or Dependabot file at the root is not replaced", () => {
  const { repo, dir } = site();
  mkdirSync(join(repo, ".github", "workflows"), { recursive: true });
  writeFileSync(join(repo, ".github", "workflows", "docs.yml"), "mine\n");
  writeFileSync(join(repo, ".github", "dependabot.yml"), "theirs\n");
  const config = configFrom(base, dir);
  const changes = applyInit(dir, config, { repoRoot: repo });
  expect(readFileSync(join(repo, ".github", "workflows", "docs.yml"), "utf8")).toBe("mine\n");
  expect(readFileSync(join(repo, ".github", "dependabot.yml"), "utf8")).toBe("theirs\n");
  expect(changes.filter((c) => c.includes("already exists"))).toHaveLength(2);
  expect(existsSync(join(dir, ".github", "workflows", "docs.yml"))).toBe(true);
  // --force replaces the workflow (an update of the template), never the Dependabot file.
  applyInit(dir, config, { repoRoot: repo, force: true });
  expect(readFileSync(join(repo, ".github", "workflows", "docs.yml"), "utf8")).toContain(
    "working-directory: docs-site",
  );
  expect(readFileSync(join(repo, ".github", "dependabot.yml"), "utf8")).toBe("theirs\n");
});

test("--no-workflow leaves the workflow where it is and --dry-run writes nothing", () => {
  const { repo, dir } = site();
  const config = configFrom(base, dir);
  const before = readFileSync(join(dir, "docs.config.json"), "utf8");
  applyInit(dir, config, { repoRoot: repo, dryRun: true });
  expect(readFileSync(join(dir, "docs.config.json"), "utf8")).toBe(before);
  expect(existsSync(join(dir, "public", "CNAME"))).toBe(false);
  expect(existsSync(join(repo, ".github"))).toBe(false);
  applyInit(dir, config, { repoRoot: repo, workflow: false });
  expect(existsSync(join(dir, ".github", "workflows", "docs.yml"))).toBe(true);
  expect(existsSync(join(repo, ".github"))).toBe(false);
});

test("running init again keeps what docs.config.json says, so an update never resets it", () => {
  const { repo, dir } = site();
  const first = configFrom(
    {
      ...base,
      name: "Frappe Nix",
      tagline: "My own tagline.",
      platform: "nixos",
      branch: "develop",
    },
    dir,
  );
  applyInit(dir, first, { repoRoot: repo });
  // No options at all: everything comes from the file.
  expect(configFrom({}, dir)).toEqual(first);
  const projectBefore = readFileSync(join(dir, "project.json"), "utf8");
  applyInit(dir, configFrom({}, dir), { repoRoot: repo, force: true });
  expect(readFileSync(join(dir, "project.json"), "utf8")).toBe(projectBefore);
  // An option still changes it.
  expect(configFrom({ tagline: "Another." }, dir)).toMatchObject({
    tagline: "Another.",
    name: "Frappe Nix",
    platform: "nixos",
  });
});

test("the template's own values are not taken for a configuration", () => {
  const { dir } = site();
  expect(() => configFrom({ repo: "https://github.com/Avunu/unknown" }, dir)).toThrow(/--name/);
});

test("the licence file of the repository is named in the error that asks for --license", () => {
  const { dir } = site({ LICENSE: "\nMIT License\n\nCopyright (c) 2026\n" });
  expect(() => decide({ repo: "https://github.com/Avunu/no-license" }, dir)).toThrow(
    /LICENSE starts with "MIT License"/,
  );
});

test("changing the default branch later rewrites the workflow that was moved to the root", () => {
  const { repo, dir } = site();
  applyInit(dir, configFrom({ ...base, branch: "develop" }, dir), { repoRoot: repo });
  const file = join(repo, ".github", "workflows", "docs.yml");
  expect(readFileSync(file, "utf8")).toContain('branches: ["develop"]');
  const changes = applyInit(dir, configFrom({ branch: "18.0" }, dir), { repoRoot: repo });
  expect(readFileSync(file, "utf8")).toContain('branches: ["18.0"]');
  expect(changes.some((c) => c.includes("publishes from 18.0 (was develop)"))).toBe(true);
  // The same branch again changes nothing, and a dry run writes nothing.
  expect(
    applyInit(dir, configFrom({}, dir), { repoRoot: repo }).some((c) => c.includes("was")),
  ).toBe(false);
  applyInit(dir, configFrom({ branch: "main" }, dir), { repoRoot: repo, dryRun: true });
  expect(readFileSync(file, "utf8")).toContain('branches: ["18.0"]');
  // A trigger the repository changed to several branches is left alone.
  writeFileSync(file, readFileSync(file, "utf8").replace('["18.0"]', '["18.0", "19.0"]'));
  applyInit(dir, configFrom({ branch: "20.0" }, dir), { repoRoot: repo });
  expect(readFileSync(file, "utf8")).toContain('branches: ["18.0", "19.0"]');
});
