import { afterAll, expect, test } from "bun:test";
import { atLeast, checkJx, checkSlug, MINIMUM_JX, preflight } from "./preflight.ts";
import { cleanup, makeTree } from "./lib/test-utils.ts";

afterAll(cleanup);

const config = {
  name: "frappe-nix",
  tagline: "Reusable Nix infrastructure for Frappe benches.",
  slug: "frappe-nix",
  platform: "nixos",
  repo: "https://github.com/Avunu/frappe-nix",
  domain: "frappe-nix.avunu.net",
  license: "MIT",
};

const repo = (
  over: {
    config?: object;
    project?: object;
    cname?: string | null;
    docs?: boolean;
    /** Installed @jxsuite versions by package name; null leaves a package out; absent installs the minimum. */
    jx?: Record<string, string | null>;
    snapshot?: string[] | null;
    docsFiles?: Record<string, string>;
  } = {},
) => {
  const files: Record<string, string> = {
    "docs-site/docs.config.json": JSON.stringify(over.config ?? config),
    "docs-site/project.json": JSON.stringify(
      over.project ?? { name: "frappe-nix", url: "https://frappe-nix.avunu.net" },
    ),
  };
  if (over.cname !== null) files["docs-site/public/CNAME"] = over.cname ?? "frappe-nix.avunu.net\n";
  if (over.docs !== false) files["docs/README.md"] = "# Home\n";
  if (over.docsFiles) {
    for (const [name, text] of Object.entries(over.docsFiles)) files[`docs/${name}`] = text;
    if (!("README.md" in over.docsFiles)) delete files["docs/README.md"];
  }
  if (over.snapshot)
    files["docs-site/data/projects.snapshot.json"] = JSON.stringify({
      projects: over.snapshot.map((slug) => ({ slug })),
    });
  for (const [name, minimum] of Object.entries(MINIMUM_JX)) {
    const version = over.jx && name in over.jx ? over.jx[name] : minimum;
    if (version)
      files[`docs-site/node_modules/@jxsuite/${name}/package.json`] = JSON.stringify({ version });
  }
  return `${makeTree(files)}/docs-site`;
};

test("a set-up site passes", () => {
  expect(preflight(repo()).errors).toEqual([]);
});

test("the template's placeholder values are an error that names init", () => {
  const errors = preflight(
    repo({
      config: { ...config, name: "Project Name", slug: "project-name" },
      project: { name: "Project Name", url: "https://project-name.avunu.net" },
      cname: "project-name.avunu.net\n",
    }),
  ).errors;
  expect(errors.some((e) => e.includes("init.ts"))).toBe(true);
});

test("project.json and docs.config.json must agree", () => {
  const errors = preflight(
    repo({ project: { name: "other", url: "https://other.avunu.net" } }),
  ).errors;
  expect(errors.some((e) => e.includes("does not match docs.config.json"))).toBe(true);
});

test("the CNAME must say the configured domain", () => {
  expect(
    preflight(repo({ cname: "other.avunu.net\n" })).errors.some((e) => e.includes("CNAME")),
  ).toBe(true);
  expect(preflight(repo({ cname: null })).errors).toEqual([]);
});

test("the docs folder is required", () => {
  expect(preflight(repo({ docs: false })).errors.some((e) => e.includes("../docs"))).toBe(true);
});

test("an invalid configuration lists what is wrong", () => {
  const errors = preflight(repo({ config: { ...config, platform: "erpnext" } })).errors;
  expect(errors.some((e) => e.includes("platform"))).toBe(true);
});

test("a Jx older than the release this site needs is an error that says what to do", () => {
  const dir = repo({ jx: { parser: "1.8.2", compiler: "4.0.2" } });
  const message = checkJx(dir)!;
  expect(message).toContain(
    "@jxsuite/parser (1.8.2) is older than this site needs (2.0.0 or newer)",
  );
  expect(message).toContain(
    "@jxsuite/compiler (4.0.2) is older than this site needs (5.0.0 or newer)",
  );
  expect(message).toContain("bun update");
  expect(message).not.toContain("link:jx");
  expect(preflight(dir).errors.some((e) => e.includes("is older than this site needs"))).toBe(true);
  expect(checkJx(repo({ jx: { parser: null } }))).toContain("@jxsuite/parser is not installed");
  expect(checkJx(repo())).toBeNull();
  expect(
    checkJx(repo({ jx: { parser: "2.3.1", compiler: "5.1.0", search: "0.10.0" } })),
  ).toBeNull();
});

test("version comparison is numeric, not alphabetical", () => {
  expect(atLeast("2.0.0", "2.0.0")).toBe(true);
  expect(atLeast("10.0.0", "2.0.0")).toBe(true);
  expect(atLeast("0.10.0", "0.4.0")).toBe(true);
  expect(atLeast("4.0.2", "4.0.3")).toBe(false);
  expect(atLeast("1.99.99", "2.0.0")).toBe(false);
  expect(atLeast("2.0.0-rc.1", "2.0.0")).toBe(true);
});

test("the placeholder tagline is an error, init has to be given a real one", () => {
  const errors = preflight(
    repo({
      config: {
        ...config,
        tagline: "One sentence that says what this project does and who it is for.",
      },
    }),
  ).errors;
  expect(errors.some((e) => e.includes("template values"))).toBe(true);
});

test("a docs folder needs a README.md or an index.md for the home page", () => {
  const none = preflight(repo({ docsFiles: { "a.md": "# A\n" } })).errors;
  expect(none.some((e) => e.includes("docs/README.md is missing"))).toBe(true);
  expect(preflight(repo({ docsFiles: { "index.md": "# Home\n" } })).errors).toEqual([]);
  expect(preflight(repo({ docsFiles: { "Readme.md": "# Home\n" } })).errors).toEqual([]);
});

test("a slug the catalog spells differently is an error, one it does not know is a warning", () => {
  const dir = repo({ snapshot: ["erpnext_taskview", "frappe-nix"] });
  expect(checkSlug(dir, "frappe-nix")).toEqual({});
  expect(checkSlug(dir, "erpnext_taskview")).toEqual({});
  const wrong = checkSlug(dir, "erpnext-taskview");
  expect(wrong.error).toContain('"erpnext_taskview"');
  const unknown = checkSlug(dir, "brand-new");
  expect(unknown.error).toBeUndefined();
  expect(unknown.warning).toContain("not in data/projects.snapshot.json");
  // No snapshot (or an empty one): nothing to compare with.
  expect(checkSlug(repo(), "anything")).toEqual({});
  const report = preflight(repo({ snapshot: ["erpnext_taskview"] }));
  expect(report.errors).toEqual([]);
  expect(report.warnings).toHaveLength(1);
  const mismatched = preflight(
    repo({ config: { ...config, slug: "erpnext-taskview" }, snapshot: ["erpnext_taskview"] }),
  );
  expect(mismatched.errors.some((e) => e.includes("erpnext_taskview"))).toBe(true);
});
