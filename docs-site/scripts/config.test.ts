import { expect, test } from "bun:test";
import {
  type DocsConfig,
  PLACEHOLDER_TAGLINE,
  isPlaceholder,
  validateConfig,
} from "./lib/config.ts";

const valid: DocsConfig = {
  name: "frappe-nix",
  tagline: "Reusable Nix infrastructure for Frappe benches.",
  slug: "frappe-nix",
  platform: "nixos",
  repo: "https://github.com/Avunu/frappe-nix",
  domain: "frappe-nix.avunu.net",
  license: "MIT",
};

test("a complete configuration is valid", () => {
  expect(validateConfig(valid)).toEqual([]);
  expect(validateConfig({ ...valid, branch: "18.0" })).toEqual([]);
});

test("every required key is named when it is missing", () => {
  const problems = validateConfig({});
  for (const key of ["name", "tagline", "slug", "platform", "repo", "domain", "license"]) {
    expect(problems.some((p) => p.includes(`"${key}" is required`))).toBe(true);
  }
});

test("values are checked, not just present", () => {
  expect(validateConfig({ ...valid, slug: "Frappe Nix" })[0]).toContain("slug");
  expect(validateConfig({ ...valid, slug: "erpnext_taskview" })).toEqual([]);
  expect(validateConfig({ ...valid, platform: "erpnext" as DocsConfig["platform"] })[0]).toContain(
    "platform",
  );
  expect(validateConfig({ ...valid, repo: "git@github.com:Avunu/x.git" })[0]).toContain("repo");
  expect(validateConfig({ ...valid, repo: "https://gitlab.com/a/b" })[0]).toContain("repo");
  expect(validateConfig({ ...valid, domain: "https://x.avunu.net" })[0]).toContain("domain");
  expect(validateConfig({ ...valid, domain: "localhost" })[0]).toContain("domain");
  expect(validateConfig({ ...valid, tagline: "x".repeat(201) })[0]).toContain("tagline");
});

test("the template values are recognised as placeholders", () => {
  expect(isPlaceholder({ name: "Project Name", slug: "project-name" })).toBe(true);
  expect(isPlaceholder(valid)).toBe(false);
});

test("the template's tagline counts as a placeholder, so a site cannot ship with it", () => {
  expect(isPlaceholder({ ...valid, tagline: PLACEHOLDER_TAGLINE })).toBe(true);
});

test("a branch must be a plain name that is safe in a workflow file and a URL", () => {
  for (const ok of ["main", "develop", "18.0", "release/1.x", "feat_x-y"])
    expect(validateConfig({ ...valid, branch: ok })).toEqual([]);
  for (const bad of ["", "a b", 'a"b', "a;b", "../x", "a//b", "-x", "a\nb", "$(x)"])
    expect(validateConfig({ ...valid, branch: bad })[0], bad).toContain("branch");
});
