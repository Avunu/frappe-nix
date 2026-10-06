// Finds Markdown that the site cannot render the way GitHub and Obsidian do. Jx drops or reshapes a
// few common constructs without a word, and a page that quietly loses a sentence is worse than a
// build that says so. The rules, each found while building the real READMEs of Avunu's repositories:
//
//   error    reference-style links ([text][ref] and its definition): the text of every link that uses
//            one vanishes, as does an image's alt text
//   error    footnotes ([^1]): the marker and the note both vanish
//   warning  inline HTML elements (<kbd>, <b>, <sub>, <a href> around text): the text stays but sits
//            outside the element, so the formatting or the link is lost
//   warning  <a> around an <img> in a paragraph (badges): the image stays, the link is lost
//   warning  an HTML block that a blank line ends before its closing tag (<div align="center">,
//            <details>): the wrapper is left empty and its content follows it
//   warning  task-list checkboxes (- [ ]) are shown as plain list items
//   warning  column alignment in tables is not applied
//   warning  ${...} in a link destination is evaluated by Jx and drops the link
//
// Errors fail a strict build (CI); warnings are printed. `bun run build` runs this on docs/.
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join, sep } from "node:path";
import { isExcluded, isPublished } from "./docs.ts";
import { parseFrontmatter } from "./frontmatter.ts";
import { destinations, lines, withoutCode } from "./markdown.ts";

export interface LintIssue {
  /** Path inside docs/, `/`-separated. */
  file: string;
  /** 1-based line in the file. */
  line: number;
  level: "error" | "warning";
  rule: string;
  message: string;
}

/** Inline elements whose content Jx moves out of the element. */
const INLINE =
  /<(kbd|b|i|em|strong|u|s|sub|sup|span|mark|small|abbr|del|ins|code|q|cite|var|samp|font|big|tt|a)(?=[\s/>])[^>]*>/gi;
/** The block-level tag names that start an HTML block (CommonMark type 6). */
const BLOCK_TAG =
  /^ {0,3}<\/?(?:address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|header|hr|html|iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|search|section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul)(?:\s|\/?>|$)/i;
/** Containers that lose their content to a blank line. */
const CONTAINERS = ["div", "details", "section", "center", "table", "blockquote", "p", "ul", "ol"];

const count = (text: string, pattern: RegExp) => (text.match(pattern) ?? []).length;

/** The issues of one Markdown file. `skip` is the number of leading lines (frontmatter) to ignore. */
export function lintMarkdown(source: string, file: string, skip = 0): LintIssue[] {
  const issues: LintIssue[] = [];
  const add = (line: number, level: LintIssue["level"], rule: string, message: string) =>
    issues.push({ file, line: line + 1, level, rule, message });

  const all = lines(source).slice(skip);
  // The end condition of the HTML block the scan is in, if any.
  let block: { end: "blank" | "comment" | "raw"; tag?: string } | null = null;
  for (let at = 0; at < all.length; at++) {
    const line = all[at]!;
    if (line.code) {
      block = null;
      continue;
    }
    const text = line.text;
    const blank = text.trim() === "";

    if (block) {
      if (block.end === "blank" && blank) block = null;
      else if (block.end === "comment" && text.includes("-->")) block = null;
      else if (block.end === "raw" && block.tag && new RegExp(`</${block.tag}>`, "i").test(text))
        block = null;
      continue;
    }
    if (blank) continue;

    // HTML blocks: their content is not parsed as Markdown, and the inline rules do not apply in them.
    if (/^ {0,3}<!--/.test(text)) {
      if (!text.includes("-->")) block = { end: "comment" };
      continue;
    }
    const raw = /^ {0,3}<(pre|script|style|textarea)\b/i.exec(text);
    if (raw) {
      if (!new RegExp(`</${raw[1]}>`, "i").test(text)) block = { end: "raw", tag: raw[1]! };
      continue;
    }
    if (BLOCK_TAG.test(text)) {
      // The block runs to the first blank line; check that its containers close inside it.
      let end = at;
      while (end + 1 < all.length && all[end + 1]!.text.trim() !== "" && !all[end + 1]!.code) end++;
      const blockText = all
        .slice(at, end + 1)
        .map((l) => l.text)
        .join("\n");
      for (const tag of CONTAINERS) {
        const opens = count(blockText, new RegExp(`<${tag}(?=[\\s>])`, "gi"));
        const closes = count(blockText, new RegExp(`</${tag}\\s*>`, "gi"));
        if (opens > closes) {
          add(
            line.index,
            "warning",
            "html-block-split",
            `<${tag}> is closed by a blank line before its </${tag}>: Markdown ends an HTML block there, so the site shows the content after an empty <${tag}>. Remove the blank lines inside it, or drop the element.`,
          );
          break;
        }
      }
      at = end;
      continue;
    }
    if (/^ {0,3}<\/?[a-z][\w-]*(?:\s[^<>]*)?\/?>\s*$/i.test(text)) {
      // A complete tag alone on a line (an <img>, a <br>) starts an HTML block that a blank line ends.
      block = { end: "blank" };
      continue;
    }

    // From here the line is Markdown prose.
    const prose = withoutCode(text);
    if (/^ {0,3}\[\^[^\]\s]+\]:/.test(prose)) {
      add(
        line.index,
        "error",
        "footnote",
        "Footnotes are not rendered: the marker and the note both disappear. Put the note in the sentence, or in a callout (> [!NOTE]).",
      );
    } else if (
      /^ {0,3}\[[^\]^][^\]]*\]:\s*(?:<[^>]*>|\S+)(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?\s*$/.test(
        prose,
      )
    ) {
      add(
        line.index,
        "error",
        "reference-link",
        "Reference-style links are not rendered: every link that uses this definition loses its text. Write the links inline: [text](url).",
      );
    }
    if (/^\s*(?:[-*+]|\d+[.)])\s+\[[ xX]\]\s/.test(prose)) {
      add(
        line.index,
        "warning",
        "task-list",
        "Task-list checkboxes are shown as plain list items. Use a plain list, or write the state in words.",
      );
    }
    if (/^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(prose) && prose.includes(":")) {
      add(
        line.index,
        "warning",
        "table-alignment",
        "Column alignment (:--, :-:, --:) is not applied: every column is left-aligned.",
      );
    }
    for (const dest of destinations(text)) {
      if (dest.value.includes("${")) {
        add(
          line.index,
          "warning",
          "template-link",
          `The link "${dest.value.slice(0, 50)}" contains \${...}, which Jx runs as an expression (the address changes, or the link is lost when the expression cannot be evaluated; in the text around a link it stays as written). Write it as %24%7B...%7D.`,
        );
      }
    }
    const tags = new Set<string>();
    for (const m of prose.matchAll(INLINE)) {
      const tag = m[1]!.toLowerCase();
      if (tag === "a") {
        const rest = prose.slice((m.index ?? 0) + m[0].length);
        const wraps = /^\s*<img\b[^>]*>\s*<\/a>/i.test(rest);
        if (tags.has(wraps ? "a-badge" : "a")) continue;
        tags.add(wraps ? "a-badge" : "a");
        add(
          line.index,
          "warning",
          wraps ? "html-badge" : "html-inline",
          wraps
            ? "An <a> around an <img> in a paragraph loses its link (the image stays). Write it as Markdown: [![alt](image)](url)."
            : "An inline <a href> keeps its text but loses the link. Write it as Markdown: [text](url).",
        );
      } else {
        tags.add(tag);
      }
    }
    const elements = [...tags].filter((tag) => tag !== "a" && tag !== "a-badge");
    if (elements.length > 0) {
      add(
        line.index,
        "warning",
        "html-inline",
        `Inline ${elements.map((tag) => `<${tag}>`).join(", ")} keeps its text but loses the element (the content is moved outside it). Use Markdown (**bold**, \`code\`) or plain text.`,
      );
    }
  }
  return issues;
}

function walk(root: string, dir = ""): string[] {
  const out: string[] = [];
  for (const name of readdirSync(join(root, dir)).sort()) {
    const rel = dir ? `${dir}/${name}` : name;
    if (isExcluded(rel)) continue;
    const full = join(root, rel.split("/").join(sep));
    if (statSync(full).isDirectory()) out.push(...walk(root, rel));
    else if (/\.md$/i.test(name)) out.push(rel);
  }
  return out;
}

/** The issues of every published page under `docsDir`. */
export function lintDocs(docsDir: string): LintIssue[] {
  if (!existsSync(docsDir)) return [];
  const issues: LintIssue[] = [];
  for (const rel of walk(docsDir)) {
    const source = readFileSync(join(docsDir, rel.split("/").join(sep)), "utf8");
    let skip = 0;
    try {
      const { data, body } = parseFrontmatter(source, rel);
      if (!isPublished(data)) continue;
      skip = source.replace(/^﻿/, "").split(/\r?\n/).length - body.split(/\r?\n/).length;
    } catch {
      // A file with broken frontmatter is reported by the build with its own message.
      continue;
    }
    issues.push(...lintMarkdown(source, rel, Math.max(0, skip)));
  }
  return issues;
}

export function formatIssue(issue: LintIssue): string {
  return `docs/${issue.file}:${issue.line}  ${issue.message}`;
}
