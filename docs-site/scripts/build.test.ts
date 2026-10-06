import { expect, test } from "bun:test";
import { problemsIn } from "./build.ts";

test("document problems in jx build output are found, hosting hints are not", () => {
  const output = [
    "Building site from /x...",
    'Content links: "docs": "a.md" links to "b.md", which is not published (left out by where.draft); it renders as plain text.',
    "Content routes: two entries share /docs/a/",
    'Warning: $paths for content type "docs": 1 routed entry has a route that no dynamic page produces (/docs)',
    "GitHub Pages ignores dist/_headers and serves its own Cache-Control",
    "Done: 8 routes → 48 files",
  ].join("\n");
  const problems = problemsIn(output);
  expect(problems).toHaveLength(3);
  expect(problems.every((p) => !p.includes("GitHub Pages"))).toBe(true);
});

test("a clean build has no problems", () => {
  expect(problemsIn("Building site...\n\nDone: 8 routes → 48 files\n")).toEqual([]);
});

test("every Content and Warning line of Jx is a problem, not only links and routes", () => {
  const output = [
    'Content validation: "docs/a" field "draft" expected boolean, got string',
    '  Content callouts: "docs": "README.md" has a [!QUESTION] callout, but "question" is not an enabled type',
    'Content type "docs": entry "README" references missing asset "x.png"',
    'Content relationships: "docs" field "next" references unknown content type "x"',
    "Warning: Referenced asset not found: /x.png",
    "Error compiling /docs: boom",
    "Done: 8 routes -> 48 files",
    "GitHub Pages ignores dist/_headers and serves its own Cache-Control",
  ].join("\n");
  expect(problemsIn(output)).toHaveLength(6);
});
