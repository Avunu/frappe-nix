// The documentation files as the site sees them: which Markdown files are pages, what each is
// called and where it is published. The rules mirror the `docs` content type in project.json
// (exclude, where, route, indexRoute), because scripts/nav.ts writes the sidebar before Jx loads
// the same files; scripts/check-links.ts proves after the build that the two agree.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, sep } from "node:path";
import { firstHeading, firstParagraph, parseFrontmatter } from "./frontmatter.ts";
import { humanize, slugifyPath } from "./slug.ts";

/** Where the documentation is published on the site (the `route` templates in project.json). */
export const DOCS_PREFIX = "/docs";

export interface DocFile {
  /** Path inside docs/, `/`-separated, with the extension: `guides/Configuration.md`. */
  rel: string;
  /** Folder inside docs/ (`""` at the top), `/`-separated. */
  dir: string;
  /** File name without the extension. */
  base: string;
  /** README.md or index.md: the page that stands for its folder. */
  isIndex: boolean;
  data: Record<string, unknown>;
  body: string;
}

/** `README` and `index` stand for their folder (the Jx rule, for any case). */
export function isIndexName(file: string): boolean {
  return /^(?:index|readme)\.[^./]+$/i.test(file);
}

/**
 * Whether a path is left out of the collection: dot files and folders, `_`-prefixed files and
 * folders, and node_modules. The `exclude` list of the docs content type in project.json says the
 * same in glob form.
 */
export function isExcluded(rel: string): boolean {
  return rel
    .split("/")
    .some((part) => part.startsWith(".") || part.startsWith("_") || part === "node_modules");
}

/** The `where` clause of the docs content type: `draft: true` and `publish: false` keep a page out. */
export function isPublished(data: Record<string, unknown>): boolean {
  return data.draft !== true && data.publish !== false;
}

function walk(root: string, dir = ""): string[] {
  const here = join(root, dir);
  const out: string[] = [];
  for (const name of readdirSync(here).sort()) {
    const rel = dir ? `${dir}/${name}` : name;
    if (isExcluded(rel)) continue;
    const stat = statSync(join(root, rel));
    if (stat.isDirectory()) out.push(...walk(root, rel));
    else if (name.toLowerCase().endsWith(".md")) out.push(rel);
  }
  return out;
}

/** Reads every published page under `root`. Files come back in the order Jx reads them. */
export function readDocs(root: string): DocFile[] {
  const files: DocFile[] = [];
  for (const rel of walk(root)) {
    const { data, body } = parseFrontmatter(
      readFileSync(join(root, rel.split("/").join(sep)), "utf8"),
      rel,
    );
    if (!isPublished(data)) continue;
    const slash = rel.lastIndexOf("/");
    const name = rel.slice(slash + 1);
    files.push({
      rel,
      dir: slash === -1 ? "" : rel.slice(0, slash),
      base: name.replace(/\.[^.]+$/, ""),
      isIndex: isIndexName(name),
      data,
      body,
    });
  }
  return files;
}

/** The URL a file is published at: `route` for a page, `indexRoute` for a folder's README. */
export function urlFor(file: Pick<DocFile, "dir" | "base" | "isIndex">): string {
  const path = file.isIndex
    ? slugifyPath(file.dir)
    : slugifyPath(file.dir ? `${file.dir}/${file.base}` : file.base);
  return `${DOCS_PREFIX}/${path ? `${path}/` : ""}`;
}

const text = (value: unknown): string => (typeof value === "string" ? value.trim() : "");

/** frontmatter `title`, else the first `# Heading`, else the file or folder name. */
export function titleOf(file: DocFile, homeName = "Documentation"): string {
  const fromFrontmatter = text(file.data.title);
  if (fromFrontmatter) return fromFrontmatter;
  const heading = firstHeading(file.body);
  if (heading) return heading;
  if (file.isIndex) {
    const folder = file.dir.slice(file.dir.lastIndexOf("/") + 1);
    return folder ? humanize(folder) : homeName;
  }
  return humanize(file.base);
}

/** The sidebar label: frontmatter `nav_title`, else the title. */
export function labelOf(file: DocFile, homeName?: string): string {
  return text(file.data.nav_title) || titleOf(file, homeName);
}

/** frontmatter `description`, else the first paragraph of the page. */
export function descriptionOf(file: DocFile): string {
  return text(file.data.description) || firstParagraph(file.body);
}

/** frontmatter `order` as a number, or Infinity when absent (pages without one sort last). */
export function orderOf(file: Pick<DocFile, "data">): number {
  const value = file.data.order;
  return typeof value === "number" && Number.isFinite(value) ? value : Number.POSITIVE_INFINITY;
}
