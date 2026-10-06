import { expect, test } from "bun:test";
import { firstHeading, firstParagraph, inlineText, parseFrontmatter } from "./lib/frontmatter.ts";

test("splits frontmatter from the body", () => {
  const { data, body } = parseFrontmatter(
    "---\ntitle: A page\norder: 2\ntags: [a, b]\n---\n\nBody text\n",
  );
  expect(data).toEqual({ title: "A page", order: 2, tags: ["a", "b"] });
  expect(body).toBe("\nBody text\n");
});

test("a file without frontmatter is all body", () => {
  expect(parseFrontmatter("# Title\n\ntext")).toEqual({ data: {}, body: "# Title\n\ntext" });
  expect(parseFrontmatter("---\n---\ntext").data).toEqual({});
});

test("bad YAML names the file", () => {
  expect(() => parseFrontmatter("---\ntitle: [unclosed\n---\n", "guides/a.md")).toThrow(
    /guides\/a\.md.*not valid YAML/,
  );
  expect(() => parseFrontmatter("---\n- a\n- b\n---\n", "b.md")).toThrow(/mapping/);
});

test("the first heading is found outside code fences, in ATX and setext form", () => {
  expect(firstHeading("Intro\n\n# The Title\n\ntext")).toBe("The Title");
  expect(firstHeading("```bash\n# not a heading\n```\n\n# Real `one`")).toBe("Real one");
  expect(firstHeading("Setext Title\n============\n\ntext")).toBe("Setext Title");
  expect(firstHeading("## only h2\n")).toBeNull();
  expect(firstHeading("# [Linked](x.md) title #")).toBe("Linked title");
});

test("the first paragraph skips headings, lists, tables, quotes, HTML and images", () => {
  const body =
    "# T\n\n![logo](x.png)\n\n<p align=center>x</p>\n\n> [!NOTE]\n> hi\n\n- a list\n\nThe **real** paragraph with a [link](a.md) and `code`,\nacross two lines.\n\nSecond.";
  expect(firstParagraph(body)).toBe("The real paragraph with a link and code, across two lines.");
  expect(firstParagraph("# Only a heading")).toBe("");
});

test("a long paragraph is cut at a word with an ellipsis", () => {
  const text = `${"word ".repeat(60)}end`;
  const cut = firstParagraph(text, 50);
  expect(cut.length).toBeLessThanOrEqual(50);
  expect(cut.endsWith("…")).toBe(true);
  expect(cut).not.toMatch(/\s…$/);
});

test("titles keep the characters that are text: underscores in words, stars with a space, code spans", () => {
  expect(inlineText("my_file page")).toBe("my_file page");
  expect(inlineText("erpnext_taskview")).toBe("erpnext_taskview");
  expect(inlineText("Using `Array<string>` safely")).toBe("Using Array<string> safely");
  expect(inlineText("C* and 2*3 notes")).toBe("C* and 2*3 notes");
  expect(inlineText("snake_case_name works")).toBe("snake_case_name works");
  expect(inlineText("Escaped \\_underscore\\_ and \\*star\\*")).toBe(
    "Escaped _underscore_ and *star*",
  );
  expect(inlineText("``a ` b`` code")).toBe("a ` b code");
});

test("titles lose what Markdown means as formatting", () => {
  expect(inlineText("**Bold** and _it_ and __strong__ and ~~gone~~")).toBe(
    "Bold and it and strong and gone",
  );
  expect(inlineText("[Linked](x.md) and ![img](a.png) and [ref][r]")).toBe(
    "Linked and img and ref",
  );
  expect(inlineText("<https://x.org> and <b>bold</b> &amp; more")).toBe(
    "https://x.org and bold & more",
  );
});

test("a heading written in HTML is the title too", () => {
  expect(
    firstHeading('<div align="center">\n  <img src="a.png">\n  <h1>Frappix</h1>\n</div>\n\ntext'),
  ).toBe("Frappix");
  expect(firstHeading('<h1 align="center">My <b>Project</b></h1>\n\n## Sub')).toBe("My Project");
  expect(firstHeading("```html\n<h1>in a fence</h1>\n```\n\n# Real")).toBe("Real");
});
