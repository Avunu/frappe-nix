// Frontmatter and plain-text helpers for the documentation files. They read a page the way the Jx
// Markdown loader does (YAML between two --- lines at the very top) and add what the sidebar needs:
// the first heading and the first paragraph.

export interface Parsed {
  data: Record<string, unknown>;
  body: string;
}

/** Splits a Markdown source into frontmatter data and body. Throws with the file name on bad YAML. */
export function parseFrontmatter(source: string, file = "document"): Parsed {
  const text = source.replace(/^﻿/, "");
  const match = /^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/.exec(text);
  if (!match) return { data: {}, body: text };
  const body = text.slice(match[0].length);
  const yaml = match[1] ?? "";
  if (yaml.trim() === "") return { data: {}, body };
  const bun = (globalThis as { Bun?: { YAML?: { parse(text: string): unknown } } }).Bun;
  if (!bun?.YAML) throw new Error("Bun.YAML is missing: this site needs Bun 1.4 or newer.");
  let parsed: unknown;
  try {
    parsed = bun.YAML.parse(yaml);
  } catch (error) {
    throw new Error(`${file}: the frontmatter is not valid YAML (${(error as Error).message})`);
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${file}: the frontmatter must be a YAML mapping (key: value lines)`);
  }
  return { data: parsed as Record<string, unknown>, body };
}

/** Lines of the body that are not inside a fenced code block, with their original index. */
function proseLines(body: string): Array<{ line: string; index: number }> {
  const out: Array<{ line: string; index: number }> = [];
  let fence: string | null = null;
  body.split(/\r?\n/).forEach((line, index) => {
    const open = /^\s{0,3}(`{3,}|~{3,})/.exec(line);
    if (fence) {
      if (open && open[1]![0] === fence[0] && open[1]!.length >= fence.length) fence = null;
      return;
    }
    if (open) {
      fence = open[1]!;
      return;
    }
    out.push({ line, index });
  });
  return out;
}

const ENTITIES: Record<string, string> = {
  "&amp;": "&",
  "&lt;": "<",
  "&gt;": ">",
  "&quot;": '"',
  "&#39;": "'",
  "&nbsp;": " ",
};

/**
 * The plain text of a line of Markdown, as a page title shows it: links, images, emphasis and tags
 * go, and the text of code spans stays exactly as written (`Array<string>` and `my_file` are
 * kept whole). Emphasis markers are only read as such where Markdown reads them: `_` inside a word
 * (snake_case) and a `*` that is followed by a space stay.
 */
export function inlineText(markdown: string): string {
  const codes: string[] = [];
  const text = markdown
    .replaceAll(/(`+)([\s\S]*?[^`])\1(?!`)/g, (_m, _ticks, code: string) => {
      codes.push(code.replace(/^ (.*) $/, "$1"));
      return `\uE000${codes.length - 1}\uE000`;
    })
    .replaceAll(
      /\\([\\`*_{}[\]()#+.!|<>~-])/g,
      (_m, char: string) => `\uE001${char.charCodeAt(0)}\uE001`,
    )
    .replaceAll(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replaceAll(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replaceAll(/\[([^\]]*)\]\[[^\]]*\]/g, "$1")
    .replaceAll(/<((?:https?|mailto):[^>\s]+)>/g, "$1")
    .replaceAll(/<\/?[a-z][^<>]*>/gi, "")
    .replaceAll(/(\*{1,3})(?=\S)([\s\S]*?\S)\1/g, "$2")
    .replaceAll(/(?<![A-Za-z0-9_])(_{1,3})(?=\S)([\s\S]*?\S)\1(?![A-Za-z0-9_])/g, "$2")
    .replaceAll(/~~(?=\S)([\s\S]*?\S)~~/g, "$1")
    .replaceAll(/&(?:amp|lt|gt|quot|nbsp|#39);/g, (entity) => ENTITIES[entity] ?? entity)
    .replaceAll(/\s+/g, " ")
    .trim();
  return text
    .replaceAll(/\uE000(\d+)\uE000/g, (_m, i: string) => codes[Number(i)] ?? "")
    .replaceAll(/\uE001(\d+)\uE001/g, (_m, code: string) => String.fromCharCode(Number(code)));
}

/** The text of the first level-1 heading outside code fences (`# Title` or a `===` underline). */
export function firstHeading(body: string): string | null {
  const lines = proseLines(body);
  for (const [i, { line }] of lines.entries()) {
    // A heading written in HTML (<h1 align="center">Title</h1>), which READMEs use to centre it.
    const html = /^\s*(?:<[a-z][^>]*>\s*)*<h1\b[^>]*>(.+?)<\/h1>/i.exec(line);
    if (html) return inlineText(html[1]!) || null;
    const atx = /^ {0,3}#[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$/.exec(line);
    if (atx) return inlineText(atx[1]!) || null;
    const next = lines[i + 1]?.line ?? "";
    if (line.trim() !== "" && !/^\s*[#>|`-]/.test(line) && /^ {0,3}=+[ \t]*$/.test(next)) {
      return inlineText(line) || null;
    }
  }
  return null;
}

/** The first paragraph of prose, flattened to plain text and cut at a word near `max` characters. */
export function firstParagraph(body: string, max = 160): string {
  const lines = proseLines(body);
  const paragraph: string[] = [];
  for (const { line } of lines) {
    const trimmed = line.trim();
    if (trimmed === "") {
      if (paragraph.length > 0) break;
      continue;
    }
    const skippable =
      /^#{1,6}\s/.test(trimmed) ||
      /^[>|]/.test(trimmed) ||
      /^([-*+]|\d+[.)])\s/.test(trimmed) ||
      /^<\/?[a-z]/i.test(trimmed) ||
      trimmed.startsWith("![") ||
      /^\[[^\]]+\]:\s/.test(trimmed) ||
      /^(-{3,}|\*{3,}|_{3,}|={3,})$/.test(trimmed) ||
      /^:{2,}/.test(trimmed);
    if (skippable) {
      if (paragraph.length > 0) break;
      continue;
    }
    paragraph.push(trimmed);
  }
  const text = inlineText(paragraph.join(" "));
  if (text.length <= max) return text;
  const cut = text.slice(0, max - 1);
  const space = cut.lastIndexOf(" ");
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).replace(/[\s,;:.-]+$/, "")}…`;
}
