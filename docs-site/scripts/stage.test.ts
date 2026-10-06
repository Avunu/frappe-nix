import { afterAll, expect, test } from "bun:test";
import { existsSync, mkdirSync, readFileSync, statSync, utimesSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { cleanup, makeTree } from "./lib/test-utils.ts";
import { moveLeadingComment, resolveLink, stageDocs, stageMarkdown } from "./lib/stage.ts";

afterAll(cleanup);

/** A repository: docs/ with a few pages, and files elsewhere that the docs link to. */
function repo(extra: Record<string, string> = {}) {
  const root = makeTree({
    "README.md": "# The repository\n",
    LICENSE: "MIT",
    "CONTRIBUTING.md": "# Contributing\n",
    "lib/x.nix": "{}",
    "worker/README.md": "# Worker\n",
    "assets/logo.png": "png",
    "docs/README.md": "# Home\n",
    "docs/chat.md": "# Chat\n",
    "docs/guides/setup.md": "# Setup\n",
    "docs/img/flow.png": "png",
    ...extra,
  });
  return {
    root,
    options: {
      source: join(root, "docs"),
      dest: join(root, "site", ".generated", "docs"),
      repoRoot: root,
      repoUrl: "https://github.com/Avunu/app",
      branch: "main",
    },
  };
}

test("a link that resolves inside docs/ is left for Jx", () => {
  const { options } = repo();
  expect(resolveLink("chat.md", "README.md", false, options)).toBeNull();
  expect(resolveLink("../chat.md#x", "guides/setup.md", false, options)).toBeNull();
  expect(resolveLink("img/flow.png", "README.md", true, options)).toBeNull();
  expect(resolveLink("guides/", "README.md", false, options)).toBeNull();
});

test("links to files outside docs/ become GitHub URLs: blob, tree, and raw for an image", () => {
  const { options } = repo();
  const base = "https://github.com/Avunu/app";
  expect(resolveLink("../CONTRIBUTING.md", "README.md", false, options)).toBe(
    `${base}/blob/main/CONTRIBUTING.md`,
  );
  expect(resolveLink("../lib/x.nix#L3", "README.md", false, options)).toBe(
    `${base}/blob/main/lib/x.nix#L3`,
  );
  expect(resolveLink("../../worker", "guides/setup.md", false, options)).toBe(
    `${base}/tree/main/worker`,
  );
  expect(resolveLink("../assets/logo.png", "README.md", true, options)).toBe(
    `${base}/raw/main/assets/logo.png`,
  );
  // The same file linked as a page link, not an image, is a blob.
  expect(resolveLink("../assets/logo.png", "README.md", false, options)).toBe(
    `${base}/blob/main/assets/logo.png`,
  );
});

test("links written from the repository root, as in a README that was copied into docs/, are fixed", () => {
  const { options } = repo();
  const base = "https://github.com/Avunu/app";
  // Outside docs/: GitHub.
  expect(resolveLink("worker/README.md", "README.md", false, options)).toBe(
    `${base}/blob/main/worker/README.md`,
  );
  expect(resolveLink("LICENSE", "README.md", false, options)).toBe(`${base}/blob/main/LICENSE`);
  expect(resolveLink("lib/x.nix", "guides/setup.md", false, options)).toBe(
    `${base}/blob/main/lib/x.nix`,
  );
  // Inside docs/: relative to the file, so Jx can resolve the page.
  expect(resolveLink("docs/chat.md", "README.md", false, options)).toBe("chat.md");
  expect(resolveLink("docs/chat.md#top", "guides/setup.md", false, options)).toBe("../chat.md#top");
  expect(resolveLink("docs/guides/setup.md", "README.md", false, options)).toBe("guides/setup.md");
  expect(resolveLink("docs/", "README.md", false, options)).toBe("./");
});

test("a path that leaves docs/ and comes back in is written as the page's own relative path", () => {
  const { options } = repo();
  expect(resolveLink("../docs/chat.md", "README.md", false, options)).toBe("chat.md");
  expect(resolveLink("../../docs/chat.md#top", "guides/setup.md", false, options)).toBe(
    "../chat.md#top",
  );
  expect(resolveLink("../docs/", "README.md", false, options)).toBe("./");
  // ./ and a/../ are fine for Jx and stay as written.
  expect(resolveLink("./chat.md", "README.md", false, options)).toBeNull();
  expect(resolveLink("guides/../chat.md", "README.md", false, options)).toBeNull();
});

test("what cannot be resolved, and what is not a repository path, is left alone", () => {
  const { options } = repo();
  for (const value of [
    "https://example.com/a.md",
    "mailto:x@example.com",
    "#section",
    "/absolute/path",
    "//cdn.example.com/x",
    "",
    "missing.md",
    "../../../../etc/passwd",
    "?query",
  ])
    expect(resolveLink(value, "README.md", false, options)).toBeNull();
});

test("percent-encoded paths are decoded to find the file and encoded again in the URL", () => {
  const { options } = repo({ "docs/My Page.md": "# x\n", "My Notes/a (1).md": "# x\n" });
  expect(resolveLink("My%20Page.md", "README.md", false, options)).toBeNull();
  expect(resolveLink("My%20Notes/a%20(1).md", "README.md", false, options)).toBe(
    "https://github.com/Avunu/app/blob/main/My%20Notes/a%20%281%29.md",
  );
});

test("stageMarkdown rewrites links and images outside code, and reports each one", () => {
  const { options } = repo();
  const source = [
    "# Home",
    "See [chat](docs/chat.md), [worker](worker/README.md) and ![logo](assets/logo.png).",
    "",
    "```md",
    "[worker](worker/README.md) stays in a fence",
    "```",
    "",
    "Inline `[worker](worker/README.md)` stays too, and [ok](chat.md) is fine.",
    "[![badge](assets/logo.png)](LICENSE)",
  ].join("\n");
  const { text, links } = stageMarkdown(source, "README.md", options);
  expect(text.split("\n")).toEqual([
    "# Home",
    "See [chat](chat.md), [worker](https://github.com/Avunu/app/blob/main/worker/README.md) and ![logo](https://github.com/Avunu/app/raw/main/assets/logo.png).",
    "",
    "```md",
    "[worker](worker/README.md) stays in a fence",
    "```",
    "",
    "Inline `[worker](worker/README.md)` stays too, and [ok](chat.md) is fine.",
    "[![badge](https://github.com/Avunu/app/raw/main/assets/logo.png)](https://github.com/Avunu/app/blob/main/LICENSE)",
  ]);
  expect(links.map((l) => [l.line, l.from])).toEqual([
    [2, "docs/chat.md"],
    [2, "worker/README.md"],
    [2, "assets/logo.png"],
    [9, "assets/logo.png"],
    [9, "LICENSE"],
  ]);
  expect(stageMarkdown("no links here\r\nsecond\r\n", "README.md", options).text).toBe(
    "no links here\r\nsecond\r\n",
  );
});

test("a comment above the frontmatter is moved below it, and nothing else is touched", () => {
  const stamped =
    "<!-- Copyright (c) 2026, Avunu LLC -->\n\n---\ntitle: A\norder: 2\n---\n\n# A\n\ntext\n";
  expect(moveLeadingComment(stamped)).toBe(
    "---\ntitle: A\norder: 2\n---\n\n<!-- Copyright (c) 2026, Avunu LLC -->\n\n# A\n\ntext\n",
  );
  expect(moveLeadingComment("<!-- a -->\n<!-- b -->\n---\ntitle: A\n---\nbody")).toBe(
    "---\ntitle: A\n---\n\n<!-- a -->\n<!-- b -->\n\nbody",
  );
  // No frontmatter behind the comment, or no comment: nothing to do.
  expect(moveLeadingComment("<!-- c -->\n\n# Title\n")).toBeNull();
  expect(moveLeadingComment("---\ntitle: A\n---\n<!-- c -->\n")).toBeNull();
  expect(moveLeadingComment("# Title\n")).toBeNull();
  // A multi-line comment, as the Frappe copyright hook writes it.
  expect(
    moveLeadingComment(
      "<!-- Copyright (c) 2026\nFor license information, see license.txt-->\n\n---\ntitle: B\n---\nx",
    ),
  ).toContain("---\ntitle: B\n---\n\n<!-- Copyright (c) 2026\nFor license");
});

test("stageDocs copies the folder, fixes Markdown, skips dot folders and node_modules", () => {
  const { options } = repo({
    "docs/.obsidian/app.json": "{}",
    "docs/node_modules/x/index.js": "x",
    "docs/guides/notes.md": "[w](../../worker/README.md)\n",
  });
  const result = stageDocs(options);
  expect(result.files).toBe(5);
  expect(result.links).toHaveLength(1);
  expect(readFileSync(join(options.dest, "guides", "notes.md"), "utf8")).toBe(
    "[w](https://github.com/Avunu/app/blob/main/worker/README.md)\n",
  );
  expect(readFileSync(join(options.dest, "img", "flow.png"), "utf8")).toBe("png");
  expect(existsSync(join(options.dest, ".obsidian"))).toBe(false);
  expect(existsSync(join(options.dest, "node_modules"))).toBe(false);
});

test("staging again writes only what changed and removes what left docs/", () => {
  const { root, options } = repo();
  expect(stageDocs(options).written).toBe(4);
  const file = join(options.dest, "chat.md");
  const stamp = statSync(file).mtimeMs;
  utimesSync(file, new Date(Date.now() - 60_000), new Date(Date.now() - 60_000));
  const second = stageDocs(options);
  expect(second.written).toBe(0);
  expect(second.removed).toBe(0);
  expect(statSync(file).mtimeMs).toBeLessThan(stamp);
  writeFileSync(join(root, "docs", "chat.md"), "# Changed\n");
  mkdirSync(join(options.dest, "gone"), { recursive: true });
  writeFileSync(join(options.dest, "gone", "old.md"), "x");
  writeFileSync(join(options.dest, "stale.md"), "x");
  const third = stageDocs(options);
  expect(third.written).toBe(1);
  expect(third.removed).toBe(2);
  expect(existsSync(join(options.dest, "stale.md"))).toBe(false);
  expect(existsSync(join(options.dest, "gone"))).toBe(false);
});

test("a missing docs folder is an error that names it", () => {
  const { options } = repo();
  expect(() => stageDocs({ ...options, source: join(options.source, "nope") })).toThrow(
    /does not exist/,
  );
});
