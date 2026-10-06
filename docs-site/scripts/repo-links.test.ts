import { afterAll, expect, test } from "bun:test";
import { join } from "node:path";
import { cleanup, makeTree } from "./lib/test-utils.ts";
import { githubUrl, repoTarget, rewriteRepoLinks } from "./lib/repo-links.ts";

afterAll(cleanup);

function setup() {
  const repo = makeTree({
    "README.md": "x",
    LICENSE: "x",
    "lib/a.nix": "x",
    "lib/deep/b.py": "x",
    "assets/logo.png": "x",
    "docs/guides/setup.md": "x",
    "docs/guides/example.json": "{}",
    "docs-site/dist/docs/index.html": "x",
    "docs-site/dist/docs/guides/setup/index.html": "x",
  });
  return {
    repo,
    options: {
      dist: join(repo, "docs-site", "dist"),
      repoRoot: repo,
      docsDir: join(repo, "docs"),
      repoUrl: "https://github.com/Avunu/x",
      branch: "main",
    },
  };
}

test("a link finds its file next to the document first, then at the repository root", () => {
  const { options } = setup();
  expect(repoTarget("example.json", "guides", options)).toBe("docs/guides/example.json");
  expect(repoTarget("lib/a.nix", "", options)).toBe("lib/a.nix");
  expect(repoTarget("../../lib/deep/b.py", "guides", options)).toBe("lib/deep/b.py");
  expect(repoTarget("LICENSE", "guides", options)).toBe("LICENSE");
  expect(repoTarget("missing.txt", "", options)).toBeNull();
  expect(repoTarget("../../../outside", "guides", options)).toBeNull();
});

test("absolute, external and fragment-only links are never repository links", () => {
  const { options } = setup();
  for (const href of ["/lib/a.nix", "https://example.com/x", "mailto:a@b.c", "#top", "", "//cdn/x"])
    expect(repoTarget(href, "", options)).toBeNull();
});

test("GitHub addresses: blob for files, tree for folders, raw for images", () => {
  const { options } = setup();
  expect(githubUrl(options, "lib/a.nix", false)).toBe(
    "https://github.com/Avunu/x/blob/main/lib/a.nix",
  );
  expect(githubUrl(options, "lib", false)).toBe("https://github.com/Avunu/x/tree/main/lib");
  expect(githubUrl(options, "assets/logo.png", true)).toBe(
    "https://github.com/Avunu/x/raw/main/assets/logo.png",
  );
  expect(githubUrl(options, "docs/guides", false)).toBe(
    "https://github.com/Avunu/x/tree/main/docs/guides",
  );
});

test("a link that is already a built page is left alone, a repository file is rewritten", () => {
  const { options } = setup();
  const html =
    '<p><a href="guides/setup/">page</a> <a href="lib/a.nix">code</a> <a href="lib/">folder</a> <img src="assets/logo.png" alt=""> <a href="https://x.dev/">ext</a> <a href="nope">nope</a></p>';
  const { html: out, links } = rewriteRepoLinks(html, "/docs/", "", options);
  expect(out).toContain('href="guides/setup/"');
  expect(out).toContain('href="https://github.com/Avunu/x/blob/main/lib/a.nix"');
  expect(out).toContain('href="https://github.com/Avunu/x/tree/main/lib"');
  expect(out).toContain('src="https://github.com/Avunu/x/raw/main/assets/logo.png"');
  expect(out).toContain('href="nope"');
  expect(links.map((l) => l.from)).toEqual(["lib/a.nix", "lib/", "assets/logo.png"]);
});

test("a fragment survives the rewrite", () => {
  const { options } = setup();
  const { html } = rewriteRepoLinks('<a href="lib/a.nix#L10">x</a>', "/docs/", "", options);
  expect(html).toContain("/blob/main/lib/a.nix#L10");
});
