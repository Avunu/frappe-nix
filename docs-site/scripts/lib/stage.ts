// Stages docs/ for Jx: copies the documentation folder to .generated/docs (the folder the `docs`
// content type reads) and fixes the two things Jx cannot.
//
// 1. Links to files of the repository that are not pages. A README copied into docs/ is full of
//    them, written relative to the repository root (`worker/README.md`, `docs/chat.md`, `LICENSE`),
//    and a README that was written for docs/ has them as `../CONTRIBUTING.md`. Jx rewrites a link
//    only when it points at a published page; a link to a Markdown file it does not publish renders
//    as plain text, and one to any other file keeps its href and 404s. Here, before Jx reads the
//    files, every link or image whose target exists in the repository is rewritten:
//      - to a file that is outside docs/        -> a GitHub URL (/blob/, /tree/, or /raw/ for an image)
//      - written from the root, inside docs/    -> the path relative to the file (`docs/chat.md` from
//        docs/README.md becomes `chat.md`), so Jx can resolve it as a page
//    A link that resolves relative to the file, inside docs/, is left alone. One that resolves
//    nowhere is left alone too: Jx reports it, and a strict build fails on it.
// 2. A leading HTML comment before the frontmatter. Hooks that stamp a copyright line into every
//    Markdown file (Frappe apps have one) put it before the `---`, which turns the frontmatter into
//    page text. The comment is moved behind the frontmatter.
//
// The copy is incremental (a file is written only when it changed, files that left docs/ are
// removed), so the dev server's file watcher sees what the author changed and nothing else.
import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname, isAbsolute, join, posix, relative, resolve, sep } from "node:path";
import { type DocsConfig, docsDir, stagedDocsDir } from "./config.ts";
import { githubUrl } from "./repo-links.ts";
import { destinations, lines, type Destination } from "./markdown.ts";

export interface StageOptions {
  /** The repository's docs/ folder. */
  source: string;
  /** Where the copy goes (.generated/docs). */
  dest: string;
  /** The repository root, to find the files that documents link to. */
  repoRoot: string;
  /** https://github.com/<owner>/<name> */
  repoUrl: string;
  branch: string;
}

export interface StagedLink {
  /** The file inside docs/ that holds the link. */
  file: string;
  /** 1-based line. */
  line: number;
  from: string;
  to: string;
}

export interface StageResult {
  files: number;
  written: number;
  removed: number;
  links: StagedLink[];
  /** Files whose leading comment was moved behind the frontmatter. */
  comments: string[];
}

const SKIP = new Set(["node_modules"]);

/** Whether `path` is `root` or inside it. */
function inside(root: string, path: string): boolean {
  const rel = relative(root, path);
  return rel === "" || (rel !== ".." && !rel.startsWith(`..${sep}`) && !isAbsolute(rel));
}

function safeDecode(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

const encodePath = (path: string) =>
  path
    .split("/")
    .map((part) => encodeURIComponent(part).replaceAll("(", "%28").replaceAll(")", "%29"))
    .join("/");

/** A destination that must not be touched: empty, an anchor, a scheme, a protocol-relative or site-absolute address. */
function untouchable(value: string): boolean {
  return (
    value === "" ||
    value.startsWith("#") ||
    value.startsWith("/") ||
    value.startsWith("?") ||
    /^[a-z][a-z0-9+.-]*:/i.test(value)
  );
}

/**
 * Where a link written in `file` (a path inside docs/) should point, or null to leave it alone.
 * `image` selects a raw GitHub URL for a file outside docs/.
 */
export function resolveLink(
  value: string,
  file: string,
  image: boolean,
  options: StageOptions,
): string | null {
  if (untouchable(value)) return null;
  const cut = value.search(/[?#]/);
  const pathPart = cut === -1 ? value : value.slice(0, cut);
  const suffix = cut === -1 ? "" : value.slice(cut);
  const decoded = safeDecode(pathPart);
  const here = dirname(join(options.source, file.split("/").join(sep)));

  const near = resolve(here, decoded);
  if (existsSync(near)) {
    // It resolves where the author wrote it. Inside docs/ that is Jx's business, unless the path
    // climbs out of docs/ and comes back in (`../docs/chat.md`): Jx reads that as leaving the
    // collection, so it is written as the page's own relative path. Outside docs/ it is GitHub's.
    if (inside(options.source, near)) {
      const dir = posix.dirname(file) === "." ? "" : posix.dirname(file);
      const logical = posix.normalize(posix.join(dir, decoded));
      if (!(logical === ".." || logical.startsWith("../"))) return null;
      const rel = relative(here, near).split(sep).join("/");
      return `${rel === "" ? "./" : encodePath(rel)}${suffix}`;
    }
    if (!inside(options.repoRoot, near)) return null;
    return github(near, image, options) + suffix;
  }
  const root = resolve(options.repoRoot, decoded);
  if (!inside(options.repoRoot, root) || !existsSync(root)) return null;
  if (inside(options.source, root)) {
    // Written from the repository root, but the file is in docs/: make it relative to this file.
    const rel = relative(here, root).split(sep).join("/");
    return `${rel === "" ? "./" : encodePath(rel)}${suffix}`;
  }
  return github(root, image, options) + suffix;
}

function github(absolute: string, image: boolean, options: StageOptions): string {
  const repoPath = relative(options.repoRoot, absolute).split(sep).join("/");
  return githubUrl(options, repoPath, image).replaceAll("(", "%28").replaceAll(")", "%29");
}

/** Moves HTML comments that precede the frontmatter behind it. Returns null when there is nothing to move. */
export function moveLeadingComment(source: string): string | null {
  const text = source.replace(/^\uFEFF/, "");
  const lead = /^(?:[ \t]*<!--[\s\S]*?-->[ \t]*\r?\n)+(?:[ \t]*\r?\n)*/.exec(text);
  if (!lead) return null;
  const rest = text.slice(lead[0].length);
  const front = /^---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/.exec(rest);
  if (!front) return null;
  const comment = lead[0].replace(/\s+$/, "");
  const body = rest.slice(front[0].length).replace(/^(?:[ \t]*\r?\n)+/, "");
  const frontmatter = front[0].endsWith("\n") ? front[0] : `${front[0]}\n`;
  return `${frontmatter}\n${comment}\n\n${body}`;
}

/** Rewrites the link and image destinations of one Markdown file. */
export function stageMarkdown(
  source: string,
  file: string,
  options: StageOptions,
): { text: string; links: StagedLink[]; comment: boolean } {
  const moved = moveLeadingComment(source);
  const text = moved ?? source;
  const links: StagedLink[] = [];
  const out: string[] = [];
  for (const line of lines(text)) {
    if (line.code || !/\]\(/.test(line.text)) {
      out.push(line.text);
      continue;
    }
    let rewritten = line.text;
    const found: Destination[] = destinations(line.text);
    const here: StagedLink[] = [];
    // Back to front, so earlier offsets stay valid.
    for (const dest of found.reverse()) {
      const to = resolveLink(dest.value, file, dest.image, options);
      if (to === null || to === dest.value) continue;
      here.push({ file, line: line.index + 1, from: dest.value, to });
      // `to` is always percent-encoded, so it needs no angle brackets; inside them it is fine too.
      rewritten = rewritten.slice(0, dest.start) + to + rewritten.slice(dest.end);
    }
    links.push(...here.reverse());
    out.push(rewritten);
  }
  const eol = /\r\n/.test(text) ? "\r\n" : "\n";
  return { text: out.join(eol), links, comment: moved !== null };
}

function sameBytes(a: Buffer, b: Buffer): boolean {
  return a.length === b.length && a.equals(b);
}

function writeIfChanged(path: string, data: Buffer | string): boolean {
  const next = typeof data === "string" ? Buffer.from(data) : data;
  if (existsSync(path)) {
    try {
      if (sameBytes(readFileSync(path), next)) return false;
    } catch {
      // unreadable: write over it
    }
  }
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, next);
  return true;
}

function listFiles(root: string, dir = ""): string[] {
  const out: string[] = [];
  for (const name of readdirSync(join(root, dir)).sort()) {
    if (name.startsWith(".") || SKIP.has(name)) continue;
    const rel = dir ? `${dir}/${name}` : name;
    const stat = statSync(join(root, rel));
    if (stat.isDirectory()) out.push(...listFiles(root, rel));
    else if (stat.isFile()) out.push(rel);
  }
  return out;
}

/** Removes files and folders of `dest` that are not in `keep` (relative paths). Returns how many files went. */
function prune(dest: string, keep: Set<string>, dir = ""): number {
  let removed = 0;
  if (!existsSync(join(dest, dir))) return 0;
  for (const name of readdirSync(join(dest, dir))) {
    const rel = dir ? `${dir}/${name}` : name;
    const full = join(dest, rel);
    if (statSync(full).isDirectory()) {
      removed += prune(dest, keep, rel);
      if (readdirSync(full).length === 0) rmSync(full, { recursive: true, force: true });
    } else if (!keep.has(rel)) {
      rmSync(full, { force: true });
      removed++;
    }
  }
  return removed;
}

/** Copies docs/ to the staging folder, fixing Markdown files on the way. */
export function stageDocs(options: StageOptions): StageResult {
  if (!existsSync(options.source)) throw new Error(`${options.source} does not exist`);
  mkdirSync(options.dest, { recursive: true });
  const result: StageResult = { files: 0, written: 0, removed: 0, links: [], comments: [] };
  const keep = new Set<string>();
  for (const rel of listFiles(options.source)) {
    keep.add(rel);
    result.files++;
    const from = join(options.source, rel.split("/").join(sep));
    const to = join(options.dest, rel.split("/").join(sep));
    let changed: boolean;
    if (/\.md$/i.test(rel)) {
      const staged = stageMarkdown(readFileSync(from, "utf8"), rel, options);
      result.links.push(...staged.links);
      if (staged.comment) result.comments.push(rel);
      changed = writeIfChanged(to, staged.text);
    } else {
      changed = writeIfChanged(to, readFileSync(from));
    }
    if (changed) result.written++;
  }
  result.removed = prune(options.dest, keep);
  return result;
}

/** Stages docs/ for the site in `root` (the docs-site folder): the options come from docs.config.json. */
export function stageSite(root: string, config: Pick<DocsConfig, "repo" | "branch">): StageResult {
  return stageDocs({
    source: docsDir(root),
    dest: stagedDocsDir(root),
    repoRoot: join(root, ".."),
    repoUrl: config.repo,
    branch: config.branch ?? "main",
  });
}
