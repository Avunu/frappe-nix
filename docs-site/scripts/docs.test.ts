import { afterAll, expect, test } from "bun:test";
import {
  descriptionOf,
  isExcluded,
  isIndexName,
  isPublished,
  labelOf,
  orderOf,
  readDocs,
  titleOf,
  urlFor,
} from "./lib/docs.ts";
import { cleanup, makeTree } from "./lib/test-utils.ts";

afterAll(cleanup);

const page = (path: string, text: string) => ({ [path]: text });

test("README and index stand for their folder, in any case", () => {
  expect(isIndexName("README.md")).toBe(true);
  expect(isIndexName("readme.md")).toBe(true);
  expect(isIndexName("Index.md")).toBe(true);
  expect(isIndexName("Readme-first.md")).toBe(false);
});

test("dot files, underscore files and node_modules are not pages", () => {
  expect(isExcluded(".github/README.md")).toBe(true);
  expect(isExcluded("guides/_draft.md")).toBe(true);
  expect(isExcluded("_private/a.md")).toBe(true);
  expect(isExcluded("node_modules/x/README.md")).toBe(true);
  expect(isExcluded("guides/a.md")).toBe(false);
});

test("draft: true and publish: false keep a page out", () => {
  expect(isPublished({})).toBe(true);
  expect(isPublished({ draft: true })).toBe(false);
  expect(isPublished({ draft: false })).toBe(true);
  expect(isPublished({ publish: false })).toBe(false);
  expect(isPublished({ publish: true })).toBe(true);
});

test("readDocs reads published pages in order and skips the rest", () => {
  const root = makeTree({
    ...page("README.md", "# Home\n"),
    ...page("b.md", "---\ntitle: B\n---\n"),
    ...page("a.md", "A\n"),
    ...page("_skip.md", "x"),
    ...page(".hidden/x.md", "x"),
    ...page("wip.md", "---\ndraft: true\n---\n"),
    ...page("guides/README.md", "# Guides\n"),
    ...page("guides/notes.txt", "not markdown"),
    ...page("images/logo.png", "png"),
  });
  const files = readDocs(root);
  expect(files.map((f) => f.rel)).toEqual(["README.md", "a.md", "b.md", "guides/README.md"]);
  expect(files.find((f) => f.rel === "guides/README.md")).toMatchObject({
    dir: "guides",
    base: "README",
    isIndex: true,
  });
});

test("addresses follow the route templates in project.json", () => {
  const u = (rel: string) => {
    const slash = rel.lastIndexOf("/");
    const name = rel.slice(slash + 1);
    return urlFor({
      dir: slash === -1 ? "" : rel.slice(0, slash),
      base: name.replace(/\.md$/, ""),
      isIndex: isIndexName(name),
    });
  };
  expect(u("README.md")).toBe("/docs/");
  expect(u("getting-started.md")).toBe("/docs/getting-started/");
  expect(u("Install Steps.md")).toBe("/docs/install-steps/");
  expect(u("guides/README.md")).toBe("/docs/guides/");
  expect(u("Git & Dev Tools/Cheat Sheet.md")).toBe("/docs/git-and-dev-tools/cheat-sheet/");
  expect(u("a/b/index.md")).toBe("/docs/a/b/");
});

const doc = (rel: string, text: string) => {
  const root = makeTree({ [rel]: text });
  return readDocs(root)[0]!;
};

test("titles: frontmatter, then the first heading, then the file or folder name", () => {
  expect(titleOf(doc("a.md", "---\ntitle: From frontmatter\n---\n# Heading\n"))).toBe(
    "From frontmatter",
  );
  expect(titleOf(doc("a.md", "# From heading\n"))).toBe("From heading");
  expect(titleOf(doc("install_steps.md", "no heading\n"))).toBe("Install Steps");
  expect(titleOf(doc("guides/README.md", "no heading\n"))).toBe("Guides");
  expect(titleOf(doc("README.md", "no heading\n"), "frappe-nix")).toBe("frappe-nix");
});

test("the sidebar label is nav_title when there is one", () => {
  expect(labelOf(doc("a.md", "---\ntitle: A long title\nnav_title: Short\n---\n"))).toBe("Short");
  expect(labelOf(doc("a.md", "---\ntitle: A long title\n---\n"))).toBe("A long title");
});

test("descriptions: frontmatter, then the first paragraph", () => {
  expect(descriptionOf(doc("a.md", "---\ndescription: Explicit.\n---\nFirst para.\n"))).toBe(
    "Explicit.",
  );
  expect(descriptionOf(doc("a.md", "# T\n\nFirst para.\n"))).toBe("First para.");
});

test("order is a number, and pages without one sort last", () => {
  expect(orderOf({ data: { order: 3 } })).toBe(3);
  expect(orderOf({ data: { order: "3" } })).toBe(Number.POSITIVE_INFINITY);
  expect(orderOf({ data: {} })).toBe(Number.POSITIVE_INFINITY);
});
