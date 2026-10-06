// Post-build fixes for what the Jx HTML emitter does to whitespace. Same approach as avunu.net's
// scripts/tidy-html.ts, plus the highlighted-code case that pages here hit because every article
// body sits inside a component (docs-prose).
//
// 1. Between sibling nodes the emitter writes a newline (and, on pages, two spaces of indent).
//    Between block elements that is harmless; between inline siblings it renders, so `[link](/x/).`
//    comes out as "link ." and `` `code`, `` as "code ,". tidyHtml removes exactly that separator
//    and keeps every space the author wrote.
// 2. Inside a component slot the emitter also puts the newline between the highlighted tokens of a
//    code block (`<span>git</span>\n<span> </span>`), which a <pre> renders as one token per line.
//    fixHighlightedCode takes the separators back out and keeps the code's own line breaks.
// 3. Component expansion leaves empty <slot></slot> elements behind: dropEmptySlots.
// 4. GitHub Pages serves a top-level 404.html for any unknown address. Jx writes pages as
//    404/index.html, so publishNotFoundPage moves it.
import { copyFileSync, existsSync, rmSync } from "node:fs";
import { join } from "node:path";

const PROTECTED = /<(pre|textarea|script|style)\b[^>]*>[\s\S]*?<\/\1>/gi;
const RUN = /([ \t]*)(\n[ \t\n]*)/g;

function fixRun(match: string, lead: string, rest: string, offset: number, whole: string): string {
  const prev = whole[offset - 1] ?? "";
  const next = whole[offset + lead.length + rest.length] ?? "";
  // Only whitespace at a tag boundary can be the emitter's. A newline between two words is the
  // author's soft line break and stays.
  if (prev !== ">" && next !== "<" && next !== "") return match;
  const newlines = rest.split("\n").length - 1;
  if (newlines >= 2) return " ";
  const trailing = rest.length - 1; // spaces after the newline
  if (next === "<" || next === "") return lead; // before a tag the whole tail is indent
  return lead + " ".repeat(trailing >= 2 ? trailing - 2 : trailing);
}

/** Removes the emitter's separators between inline siblings. <pre>, <textarea>, <script> and <style> are never touched. */
export function tidyHtml(html: string): string {
  let out = "";
  let last = 0;
  for (const m of html.matchAll(PROTECTED)) {
    out += html.slice(last, m.index).replace(RUN, fixRun);
    out += m[0];
    last = m.index + m[0].length;
  }
  return out + html.slice(last).replace(RUN, fixRun);
}

/** Opening tag names that are void elements: they never get a closing tag, so they never nest. */
const VOID = new Set([
  "area",
  "base",
  "br",
  "col",
  "embed",
  "hr",
  "img",
  "input",
  "link",
  "meta",
  "source",
  "track",
  "wbr",
]);

/** The [start, end) offsets of every <pre>…</pre> that sits inside a custom element (a tag with a hyphen). */
function preBlocksInComponents(html: string): Array<[number, number]> {
  const blocks: Array<[number, number]> = [];
  const stack: string[] = [];
  let componentDepth = 0;
  let preStart = -1;
  let preDepth = 0;
  const tag = /<!--[\s\S]*?-->|<(\/?)([a-zA-Z][\w:-]*)\b[^>]*?(\/?)>/g;
  for (const m of html.matchAll(tag)) {
    if (!m[2]) continue;
    const name = m[2].toLowerCase();
    if (VOID.has(name) || m[3] === "/") continue;
    if (name === "script" || name === "style" || name === "textarea") {
      // Raw text elements: skip to their end so markup inside them is not read as tags.
      if (!m[1]) {
        const close = html.indexOf(`</${name}`, m.index! + m[0].length);
        if (close > -1) tag.lastIndex = html.indexOf(">", close) + 1;
      }
      continue;
    }
    if (!m[1]) {
      stack.push(name);
      if (name.includes("-")) componentDepth++;
      if (name === "pre" && preDepth === 0 && componentDepth > 0) {
        preStart = m.index!;
        preDepth = 1;
      } else if (preDepth > 0) {
        preDepth++;
      }
    } else {
      const open = stack.lastIndexOf(name);
      if (open === -1) continue;
      while (stack.length > open) {
        const closed = stack.pop()!;
        if (closed.includes("-")) componentDepth--;
        if (preDepth > 0) {
          preDepth--;
          if (preDepth === 0) blocks.push([preStart, m.index! + m[0].length]);
        }
      }
    }
  }
  return blocks;
}

/**
 * Takes the emitter's separators out of highlighted code blocks inside components. Between two
 * token spans a run of k newlines is m code newlines with a separator on each side of every one:
 * k = 2m + 1. Before the first or after the last token it is k = 2m.
 */
export function fixHighlightedCode(html: string): string {
  const blocks = preBlocksInComponents(html);
  if (blocks.length === 0) return html;
  let out = "";
  let last = 0;
  for (const [start, end] of blocks) {
    out += html.slice(last, start);
    const block = html.slice(start, end);
    out += /<code\b[^>]*\bclass="[^"]*\bshiki\b/.test(block) ? fixBlock(block) : block;
    last = end;
  }
  return out + html.slice(last);
}

function fixBlock(block: string): string {
  const open = /<code\b[^>]*>/.exec(block);
  const close = block.lastIndexOf("</code>");
  if (!open || close === -1) return block;
  const innerStart = open.index + open[0].length;
  const inner = block.slice(innerStart, close);
  const fixed = inner
    .replace(/^(\n+)(?=<span)/, (_m, run: string) => "\n".repeat(Math.floor(run.length / 2)))
    .replace(
      /(<\/span>)(\n+)(?=<span)/g,
      (_m, tagText: string, run: string) => tagText + "\n".repeat(Math.floor((run.length - 1) / 2)),
    )
    .replace(
      /(<\/span>)(\n+)$/,
      (_m, tagText: string, run: string) => tagText + "\n".repeat(Math.floor(run.length / 2)),
    );
  return block.slice(0, innerStart) + fixed + block.slice(close);
}

/** Component expansion leaves an unfilled <slot></slot> behind; it renders nothing and says nothing. */
export function dropEmptySlots(html: string): string {
  return html.replace(/<slot(?:\s[^>]*)?><\/slot>/g, "");
}

export function tidyPage(html: string): string {
  return dropEmptySlots(fixHighlightedCode(tidyHtml(html)));
}

/**
 * GitHub Pages answers an unknown URL with a top-level `404.html`. Jx writes the page
 * `pages/404.json` to `404/index.html` (trailing-slash routes), so it is moved to `404.html`; the
 * `404/` folder goes, because `/404/` would otherwise be a real, indexable-looking URL.
 */
export function publishNotFoundPage(root: string): boolean {
  const built = join(root, "404", "index.html");
  if (!existsSync(built)) return false;
  copyFileSync(built, join(root, "404.html"));
  rmSync(join(root, "404"), { recursive: true, force: true });
  return true;
}
