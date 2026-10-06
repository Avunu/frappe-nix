import { afterAll, expect, test } from "bun:test";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { readDocs } from "./lib/docs.ts";
import { buildNav } from "./lib/nav.ts";
import { cleanup, makeTree } from "./lib/test-utils.ts";
import { generateNav, writeNav } from "./nav.ts";

afterAll(cleanup);

const nav = (files: Record<string, string>, name?: string) =>
  buildNav(readDocs(makeTree(files)), name);

test("order is frontmatter order, then title, with top-level files before folders", () => {
  const { nav: n } = nav({
    "README.md": "# Home\n",
    "zeta.md": "---\ntitle: Zeta\norder: 1\n---\n",
    "alpha.md": "---\ntitle: Alpha\norder: 2\n---\n",
    "beta.md": "# Beta\n",
    "gamma.md": "# Gamma\n",
    "Reference/README.md": "# Reference\n",
    "Reference/api.md": "# API\n",
    "Guides/README.md": "---\ntitle: Guides\norder: 1\n---\n",
    "Guides/b.md": "# Second\n",
    "Guides/a.md": "---\ntitle: First\norder: 1\n---\n",
  });
  expect(n.home).toEqual({ label: "Overview", url: "/docs/" });
  expect(n.loose.map((l) => l.label)).toEqual(["Zeta", "Alpha", "Beta", "Gamma"]);
  expect(n.sections.map((s) => s.label)).toEqual(["Guides", "Reference"]);
  expect(n.sections[0]!.pages.map((p) => p.label)).toEqual(["Overview", "First", "Second"]);
  expect(n.flat.map((f) => f.url)).toEqual([
    "/docs/",
    "/docs/zeta/",
    "/docs/alpha/",
    "/docs/beta/",
    "/docs/gamma/",
    "/docs/guides/",
    "/docs/guides/a/",
    "/docs/guides/b/",
    "/docs/reference/",
    "/docs/reference/api/",
  ]);
});

test("previous and next walk the reading order", () => {
  const { nav: n } = nav({
    "README.md": "# Home\n",
    "a.md": "# A\n",
    "g/README.md": "# G\n",
    "g/b.md": "# B\n",
  });
  expect(n.pages["/docs/"]!.prev).toBeNull();
  expect(n.pages["/docs/"]!.next).toEqual({ title: "A", url: "/docs/a/" });
  expect(n.pages["/docs/a/"]!.next).toEqual({ title: "G", url: "/docs/g/" });
  expect(n.pages["/docs/g/b/"]!.prev).toEqual({ title: "G", url: "/docs/g/" });
  expect(n.pages["/docs/g/b/"]!.next).toBeNull();
});

test("a folder is a section, a folder inside it a group, deeper folders join their group", () => {
  const { nav: n } = nav({
    "README.md": "# Home\n",
    "ops/README.md": "# Operations\n",
    "ops/backup.md": "# Backup\n",
    "ops/cloud/README.md": "# Cloud\n",
    "ops/cloud/aws.md": "# AWS\n",
    "ops/cloud/deep/gcp.md": "# GCP\n",
  });
  const ops = n.sections[0]!;
  expect(ops.label).toBe("Operations");
  expect(ops.url).toBe("/docs/ops/");
  expect(ops.pages.map((p) => p.label)).toEqual(["Overview", "Backup"]);
  expect(ops.groups).toHaveLength(1);
  expect(ops.groups[0]!.label).toBe("Cloud");
  expect(ops.groups[0]!.pages.map((p) => p.url)).toEqual([
    "/docs/ops/cloud/",
    "/docs/ops/cloud/aws/",
    "/docs/ops/cloud/deep/gcp/",
  ]);
  expect(ops.urls).toContain("/docs/ops/cloud/deep/gcp/");
});

test("a folder without a README is named after the folder and has no link of its own", () => {
  const { nav: n } = nav({ "README.md": "# Home\n", "how_to/a.md": "# A\n" });
  expect(n.sections[0]).toMatchObject({ label: "How To", url: null });
});

test("hidden pages are published and reachable but not in the sidebar or previous/next", () => {
  const { nav: n } = nav({
    "README.md": "# Home\n",
    "a.md": "# A\n",
    "secret.md": "---\nhidden: true\n---\n# Secret\n",
    "z.md": "# Z\n",
  });
  expect(n.loose.map((l) => l.label)).toEqual(["A", "Z"]);
  expect(n.flat.map((f) => f.url)).not.toContain("/docs/secret/");
  expect(n.pages["/docs/secret/"]).toMatchObject({ title: "Secret", prev: null, next: null });
  expect(n.pages["/docs/a/"]!.next!.url).toBe("/docs/z/");
});

test("drafts are not in the navigation at all", () => {
  const { nav: n } = nav({ "README.md": "# Home\n", "wip.md": "---\ndraft: true\n---\n# WIP\n" });
  expect(Object.keys(n.pages)).toEqual(["/docs/"]);
});

test("two files with one address publish the first and warn", () => {
  const { nav: n, warnings } = nav({
    "README.md": "# Home\n",
    "Guide.md": "# One\n",
    "guide.md": "# Two\n",
  });
  expect(Object.keys(n.pages).filter((u) => u.includes("guide"))).toEqual(["/docs/guide/"]);
  expect(warnings).toHaveLength(1);
  expect(warnings[0]).toContain("same address");
});

test("docs/README.md is required", () => {
  expect(() => nav({ "a.md": "# A\n" })).toThrow(/docs\/README\.md is missing/);
});

test("the sidebar starts every section open when the docs are small", () => {
  expect(nav({ "README.md": "# H\n", "a.md": "# A\n" }).nav.expandAll).toBe(true);
  const many: Record<string, string> = { "README.md": "# H\n" };
  for (let i = 0; i < 40; i++) many[`p${i}.md`] = `# P${i}\n`;
  expect(nav(many).nav.expandAll).toBe(false);
});

test("the landing page gets the first pages after the home page, with their descriptions", () => {
  const { nav: n } = nav({
    "README.md": "# H\n",
    "a.md": "# A\n\nAbout a.\n",
    "g/README.md": "# G\n\nAbout g.\n",
  });
  expect(n.featured).toEqual([
    { title: "A", description: "About a.", url: "/docs/a/", section: "" },
    { title: "G", description: "About g.", url: "/docs/g/", section: "G" },
  ]);
});

test("page info carries the title, section, edit path and description", () => {
  const { nav: n } = nav({
    "README.md": "# H\n",
    "g/README.md": "# Guides\n",
    "g/Install Steps.md": "---\ndescription: How.\n---\n# Install\n",
  });
  expect(n.pages["/docs/g/install-steps/"]).toMatchObject({
    title: "Install",
    section: "Guides",
    edit: "g/Install Steps.md",
    description: "How.",
  });
});

test("the CLI writes .generated/nav.json", () => {
  const root = makeTree({ "docs/README.md": "# Home\n\nHello.\n", "docs/a.md": "# A\n" });
  const out = join(root, ".generated", "nav.json");
  const { pages } = writeNav(join(root, "docs"), out);
  expect(pages).toBe(2);
  expect(existsSync(out)).toBe(true);
  expect(JSON.parse(readFileSync(out, "utf8")).home.url).toBe("/docs/");
  expect(generateNav(join(root, "docs")).nav.flat).toHaveLength(2);
});
