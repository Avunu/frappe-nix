// The deploy workflow is the part of the template that touches a repository's Pages and its
// tokens, so its security properties and its gates are asserted on the real file, not on a stub.
//
// In an adopting repository the same tests guard the copy at the repository root (its folder name
// and branch are whatever init wrote); a repository without that workflow skips them.
import { expect, test as bunTest } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { BRANCH, ROOT } from "./lib/config.ts";
import { workflowLocation } from "./lib/test-utils.ts";

const where = workflowLocation();
const test = where ? bunTest : bunTest.skip;
const folder = where?.folder ?? "docs-site";
const text = where ? readFileSync(where.file, "utf8") : "jobs: {}";
const workflow = Bun.YAML.parse(text) as Record<string, any>;
const jobs = workflow.jobs as Record<string, any>;
const steps = (job: string): Array<Record<string, any>> => jobs[job].steps;

test("nothing gets a token by default, and each job asks for the least it needs", () => {
  expect(workflow.permissions).toEqual({});
  expect(jobs.build.permissions).toEqual({ contents: "read" });
  expect(jobs.deploy.permissions).toEqual({ pages: "write", "id-token": "write" });
  expect(Object.keys(jobs).sort()).toEqual(["build", "deploy"]);
});

test("every action is pinned to a full commit SHA, with the release it is in a comment", () => {
  const uses = [...text.matchAll(/^\s*(?:-\s+)?uses:\s*(\S+)(.*)$/gm)];
  expect(uses.length).toBeGreaterThanOrEqual(6);
  for (const [, action, rest] of uses) {
    expect(action, `${action} is not pinned`).toMatch(/^[\w.-]+\/[\w./-]+@[0-9a-f]{40}$/);
    expect(rest, `${action} lacks its release comment`).toMatch(/#\s*v\d/);
  }
});

test("a checkout never keeps the token in the repository's git config", () => {
  const checkouts = [...steps("build"), ...steps("deploy")].filter((s) =>
    String(s.uses ?? "").startsWith("actions/checkout@"),
  );
  expect(checkouts.length).toBe(1);
  for (const step of checkouts) expect(step.with["persist-credentials"]).toBe(false);
});

test("no run step interpolates an expression: values reach the shell through env", () => {
  for (const step of [...steps("build"), ...steps("deploy")]) {
    if (typeof step.run === "string") expect(step.run, step.name).not.toContain("${{");
  }
});

test("every job is gated by the repository variable, and only the default branch deploys", () => {
  for (const job of Object.values(jobs))
    expect(String(job.if)).toContain("vars.DOCS_SITE_ENABLED == 'true'");
  const deploy = String(jobs.deploy.if);
  expect(deploy).toContain("github.event_name != 'pull_request'");
  expect(deploy).toContain(
    "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)",
  );
  expect(deploy).not.toContain("refs/heads/main");
  expect(jobs.deploy.needs).toBe("build");
  expect(jobs.deploy.environment.name).toBe("github-pages");
  expect(jobs.deploy.concurrency["cancel-in-progress"]).toBe(false);
});

test("the build runs on pull requests and on the branch init writes, for the docs and the site only", () => {
  const triggers = workflow.on as Record<string, any>;
  // The template says main; init writes the repository's default branch there.
  expect(triggers.push.branches).toHaveLength(1);
  expect(String(triggers.push.branches[0])).toMatch(BRANCH);
  if (where?.template) expect(triggers.push.branches).toEqual(["main"]);
  expect(triggers.pull_request.branches).toBeUndefined();
  for (const trigger of [triggers.pull_request, triggers.push])
    expect(trigger.paths).toEqual(["docs/**", `${folder}/**`, ".github/workflows/docs.yml"]);
  expect("workflow_dispatch" in triggers).toBe(true);
});

test("the build installs from the lockfile and runs the whole check", () => {
  const runs = steps("build").map((s) => String(s.run ?? ""));
  expect(runs.filter((r) => r.includes("bun install --frozen-lockfile")).length).toBeGreaterThan(0);
  expect(runs).toContain("bun run check");
  expect(jobs.build.defaults.run["working-directory"]).toBe(folder);
  // The catalog refresh must not be able to fail a build.
  expect(runs).toContain("bun scripts/sync-projects.ts --soft");
});

test("the site is built from what the lockfile pins: no other repository is fetched, no Jx is linked", () => {
  const list = steps("build");
  // The only checkout is the repository itself (no `repository:` input, so nothing else is fetched).
  for (const step of list.filter((s) => String(s.uses ?? "").startsWith("actions/checkout@")))
    expect(step.with.repository).toBeUndefined();
  // Nothing else downloads or links code: the install step is the only place packages arrive.
  for (const step of list) {
    const run = String(step.run ?? "");
    expect(run, String(step.name)).not.toMatch(
      /\bgit\s+clone\b|\bcurl\b|\bwget\b|link:jx|bun\s+link\b/,
    );
  }
  // The install comes before anything that runs the site's scripts.
  const names = list.map((s) => String(s.name));
  const install = names.indexOf("Install dependencies");
  expect(install).toBeGreaterThan(-1);
  expect(install).toBeLessThan(names.indexOf("Check and build"));
  // The variable that once pointed the build at a Jx commit is gone, in the file and in its comments.
  expect(text).not.toContain("DOCS_JX_REF");
  expect(text).not.toMatch(/\.jx\b/);
});

test("pull requests upload the site for review, other runs hand it to Pages", () => {
  const upload = steps("build").find((s) => String(s.uses).startsWith("actions/upload-artifact@"))!;
  expect(String(upload.if)).toContain("github.event_name == 'pull_request'");
  const pages = steps("build").find((s) =>
    String(s.uses).startsWith("actions/upload-pages-artifact@"),
  )!;
  expect(String(pages.if)).toContain("github.event_name != 'pull_request'");
  expect(pages.with.path).toBe(`${folder}/dist`);
});

// Only the template ships a Dependabot file of its own; a repository merges the entries into its own.
const templateOnly = where?.template ? bunTest : bunTest.skip;
templateOnly(
  "the Dependabot configuration covers the pinned actions and the site's packages",
  () => {
    const config = Bun.YAML.parse(
      readFileSync(join(ROOT, ".github", "dependabot.yml"), "utf8"),
    ) as {
      updates: Array<Record<string, any>>;
    };
    const ecosystems = Object.fromEntries(config.updates.map((u) => [u["package-ecosystem"], u]));
    expect(ecosystems["github-actions"].directory).toBe("/");
    expect(ecosystems.bun.directory).toBe("/docs-site");
    for (const update of config.updates) expect(update.cooldown["default-days"]).toBeGreaterThan(0);
  },
);
