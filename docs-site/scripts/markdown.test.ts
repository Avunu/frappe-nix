import { expect, test } from "bun:test";
import { codeSpans, destinations, lines, withoutCode } from "./lib/markdown.ts";

test("lines marks fenced code, with the fences themselves", () => {
  const out = lines("a\n```bash\n[x](y)\n```\nb\n~~~\ncode\n~~~\nc");
  expect(out.map((l) => l.code)).toEqual([false, true, true, true, false, true, true, true, false]);
  expect(out[1]!.fence).toBe("bash");
  // A longer fence is not closed by a shorter one, and a different character does not close it.
  const nested = lines("````md\n```\ninner\n```\n````\nafter");
  expect(nested.map((l) => l.code)).toEqual([true, true, true, true, true, false]);
  expect(lines("```\nopen forever\ntext").every((l) => l.code)).toBe(true);
  // A fence inside a list item is indented by the item, which can be four spaces or more.
  const listed = lines("10. Step\n\n    ```bash\n    [x](y.md)\n    ```\n\n    after");
  expect(listed.map((l) => l.code)).toEqual([false, false, true, true, true, false, false]);
  // Three backticks followed by more backticks on the line are a code span, not a fence.
  expect(lines("```not a fence``` but text\nnext").map((l) => l.code)).toEqual([false, false]);
  expect(lines("a\r\nb").map((l) => l.text)).toEqual(["a", "b"]);
});

test("inline code spans are found, including double-backtick ones", () => {
  const text = "a `b` c ``d ` e`` f `unclosed";
  expect(codeSpans(text).map(([a, b]) => text.slice(a, b))).toEqual(["`b`", "``d ` e``"]);
  expect(withoutCode("x `[a](b)` y")).toBe(`x${" ".repeat(10)}y`);
  expect(codeSpans("escaped \\`not code\\`")).toEqual([]);
});

test("link and image destinations are found with their offsets", () => {
  const text =
    'See [a](one.md), ![img](two.png "title") and [<c>](<three four.md>) and [d](f(g).md).';
  const found = destinations(text);
  expect(found.map((d) => d.value)).toEqual(["one.md", "two.png", "three four.md", "f(g).md"]);
  expect(found.map((d) => d.image)).toEqual([false, true, false, false]);
  expect(found.map((d) => d.angle)).toEqual([false, false, true, false]);
  for (const d of found) expect(text.slice(d.start, d.end)).toBe(d.value);
});

test("a badge (a link around an image) gives both destinations; code and escapes are skipped", () => {
  const found = destinations("[![CI](https://img.x/b.svg)](https://x/actions)");
  expect(found.map((d) => [d.value, d.image])).toEqual([
    ["https://img.x/b.svg", true],
    ["https://x/actions", false],
  ]);
  expect(destinations("`[a](b.md)` and \\[c](d.md)")).toEqual([]);
  expect(destinations("[unbalanced](a(b.md)")).toEqual([]);
  expect(destinations("[text only] (not a link)")).toEqual([]);
  expect(destinations("[empty]()").map((d) => d.value)).toEqual([""]);
});
