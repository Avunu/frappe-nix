import { afterAll, expect, test } from "bun:test";
import { formatIssue, lintDocs, lintMarkdown } from "./lib/lint.ts";
import { cleanup, makeTree } from "./lib/test-utils.ts";

afterAll(cleanup);

const rules = (source: string) =>
  lintMarkdown(source, "a.md").map((i) => `${i.level}:${i.rule}@${i.line}`);

test("ordinary Markdown has nothing to report", () => {
  expect(
    rules(
      [
        "# Title",
        "",
        "Text with **bold**, `code`, a [link](a.md), an ![image](b.png) and <https://example.com>.",
        "",
        "- a list",
        "  - nested",
        "",
        "| a | b |",
        "|---|---|",
        "| 1 | 2 |",
        "",
        "> [!NOTE]",
        "> A callout with <br> a break and an <img src=x.png> image.",
        "",
        "```html",
        "<kbd>Ctrl</kbd> in code is fine, and [ref]: not a definition",
        "```",
        "",
        '<p align="center">',
        '  <a href="https://x.org"><img src="b.svg"></a>',
        "</p>",
      ].join("\n"),
    ),
  ).toEqual([]);
});

test("reference-style link definitions and footnotes are errors: the text of the links vanishes", () => {
  expect(rules("See [the docs][d].\n\n[d]: https://example.com/docs")).toEqual([
    "error:reference-link@3",
  ]);
  expect(rules("Text[^1].\n\n[^1]: The note.")).toEqual(["error:footnote@3"]);
  expect(rules('[d]: <https://example.com/a b> "A title"\n[e]: https://example.com')).toEqual([
    "error:reference-link@1",
    "error:reference-link@2",
  ]);
  // Square brackets that are not definitions are text: a definition has one address and an optional title.
  expect(rules("[Note]: this is important\n\n[Warning]: do not")).toEqual([]);
  // Square brackets that are not definitions are text.
  expect(rules("array[0][1] and [x] and a [^caret] mention")).toEqual([]);
  expect(rules("`[d]: https://example.com`")).toEqual([]);
});

test("inline HTML elements warn: the text stays, the element is lost", () => {
  expect(rules("Press <kbd>Ctrl</kbd>+<kbd>C</kbd> and <sub>2</sub>.")).toEqual([
    "warning:html-inline@1",
  ]);
  expect(rules("A <a href='https://x.org'>link</a> in a sentence.")).toEqual([
    "warning:html-inline@1",
  ]);
  const badge = lintMarkdown('<a href="https://x.org"><img src="b.svg"></a>', "a.md");
  expect(badge.map((i) => i.rule)).toEqual(["html-badge"]);
  expect(badge[0]!.message).toContain("[![alt](image)](url)");
  // An email autolink is not an <a> element.
  expect(rules("Mail <a@b.com> or visit <https://x.org>.")).toEqual([]);
});

test("an HTML block that a blank line ends before its closing tag warns", () => {
  expect(rules('<div align="center">\n\n# Title\n\n</div>\n')).toEqual([
    "warning:html-block-split@1",
  ]);
  expect(rules("<details>\n<summary>More</summary>\n\nHidden **text**.\n\n</details>\n")).toEqual([
    "warning:html-block-split@1",
  ]);
  // Closed inside the block: fine.
  expect(rules("<details>\n<summary>More</summary>\nHidden text.\n</details>\n")).toEqual([]);
  expect(rules('<p align="center">\n  <img src="a.png">\n</p>\n')).toEqual([]);
});

test("task lists, table alignment and template expressions in links warn", () => {
  expect(rules("- [x] done\n- [ ] open")).toEqual(["warning:task-list@1", "warning:task-list@2"]);
  expect(rules("| a | b |\n|:--|--:|\n| 1 | 2 |")).toEqual(["warning:table-alignment@2"]);
  expect(rules("[x](https://x.org/${HOME})")).toEqual(["warning:template-link@1"]);
  expect(rules("[x](https://x.org/%24%7BHOME%7D)")).toEqual([]);
});

test("HTML comments, pre blocks and fences hide their content from the inline rules", () => {
  expect(rules("<!-- <kbd>x</kbd>\n[a]: b\n-->\n\ntext")).toEqual([]);
  expect(rules("<pre>\n<kbd>x</kbd>\n</pre>\n")).toEqual([]);
  expect(rules("```\n[a]: b\n- [x] t\n```\n")).toEqual([]);
});

test("lintDocs skips frontmatter, drafts and excluded files, and reports file and line", () => {
  const root = makeTree({
    "README.md":
      "---\ntitle: 'Home'\ndescription: '[x]: not a definition'\n---\n\nText <kbd>x</kbd>\n",
    "guides/a.md": "# A\n\n[r]: https://example.com\n",
    "wip.md": "---\ndraft: true\n---\n\n[r]: https://example.com\n",
    "_private.md": "[r]: https://example.com\n",
    "broken.md": "---\ntitle: [unclosed\n---\n[r]: https://example.com\n",
  });
  const issues = lintDocs(root);
  expect(issues.map((i) => `${i.file}:${i.line}:${i.rule}`)).toEqual([
    "README.md:6:html-inline",
    "guides/a.md:3:reference-link",
  ]);
  expect(formatIssue(issues[1]!)).toMatch(/^docs\/guides\/a\.md:3 {2}Reference-style links/);
});
