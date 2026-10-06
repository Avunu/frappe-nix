// Builds the sidebar, the previous/next links and the landing-page cards from the documentation
// files. Pure functions: scripts/nav.ts reads the files and writes the result to .generated/nav.json,
// which layouts/docs.json and pages/[...path].json read at build time.
import {
  type DocFile,
  DOCS_PREFIX,
  descriptionOf,
  labelOf,
  orderOf,
  titleOf,
  urlFor,
} from "./docs.ts";
import { humanize } from "./slug.ts";

export interface NavLink {
  label: string;
  url: string;
}

export interface NavGroup {
  label: string;
  /** The folder's own page, when it has a README: the group's heading is not a link, the first item is. */
  url: string | null;
  /** Every page URL inside, so the layout can open the group that holds the current page. */
  urls: string[];
  pages: NavLink[];
}

export interface NavSection extends NavGroup {
  groups: NavGroup[];
}

export interface PageInfo {
  title: string;
  description: string;
  /** The section (top-level folder) the page is in, for the eyebrow; empty for top-level pages. */
  section: string;
  prev: { title: string; url: string } | null;
  next: { title: string; url: string } | null;
  /** The file inside docs/, for the "Edit this page" link. */
  edit: string;
}

export interface NavData {
  home: NavLink;
  loose: NavLink[];
  sections: NavSection[];
  /** Few pages: every section starts open. Many: only the one holding the current page. */
  expandAll: boolean;
  pages: Record<string, PageInfo>;
  /** Reading order, home first: what previous/next walk. */
  flat: Array<{ title: string; url: string }>;
  /** Cards for the landing page: the first pages after the home page. */
  featured: Array<{ title: string; description: string; url: string; section: string }>;
}

interface Folder {
  dir: string;
  name: string;
  index: DocFile | null;
  pages: DocFile[];
  folders: Folder[];
}

const EXPAND_ALL_LIMIT = 24;
const collator = new Intl.Collator("en", { numeric: true, sensitivity: "base" });

function byOrder<T>(order: (item: T) => number, label: (item: T) => string) {
  return (a: T, b: T) => {
    const oa = order(a);
    const ob = order(b);
    if (oa !== ob) return oa < ob ? -1 : 1;
    return collator.compare(label(a), label(b));
  };
}

/** Drops pages that would share a URL with an earlier one (Jx keeps the first and warns). */
export function dedupe(files: DocFile[]): { files: DocFile[]; duplicates: string[] } {
  const seen = new Map<string, string>();
  const kept: DocFile[] = [];
  const duplicates: string[] = [];
  for (const file of files) {
    const url = urlFor(file);
    const first = seen.get(url);
    if (first) {
      duplicates.push(
        `${file.rel} has the same address as ${first} (${url}); only ${first} is published`,
      );
      continue;
    }
    seen.set(url, file.rel);
    kept.push(file);
  }
  return { files: kept, duplicates };
}

function tree(files: DocFile[]): Folder {
  const root: Folder = { dir: "", name: "", index: null, pages: [], folders: [] };
  const folders = new Map<string, Folder>([["", root]]);
  const folderFor = (dir: string): Folder => {
    const known = folders.get(dir);
    if (known) return known;
    const slash = dir.lastIndexOf("/");
    const folder: Folder = { dir, name: dir.slice(slash + 1), index: null, pages: [], folders: [] };
    folders.set(dir, folder);
    folderFor(slash === -1 ? "" : dir.slice(0, slash)).folders.push(folder);
    return folder;
  };
  for (const file of files) {
    const folder = folderFor(file.dir);
    if (file.isIndex) folder.index = folder.index ?? file;
    else folder.pages.push(file);
  }
  return root;
}

const folderOrder = (folder: Folder) =>
  folder.index ? orderOf(folder.index) : Number.POSITIVE_INFINITY;
const folderLabel = (folder: Folder) =>
  folder.index ? labelOf(folder.index) : humanize(folder.name);

/** Every page below a folder in reading order: the folder's pages first, then each subfolder. */
function collect(folder: Folder): DocFile[] {
  const out: DocFile[] = [];
  if (folder.index) out.push(folder.index);
  out.push(...[...folder.pages].sort(byOrder<DocFile>(orderOf, (f) => labelOf(f))));
  for (const sub of [...folder.folders].sort(byOrder(folderOrder, folderLabel)))
    out.push(...collect(sub));
  return out;
}

const link = (file: DocFile, label = labelOf(file)): NavLink => ({ label, url: urlFor(file) });

function group(folder: Folder, visible: (file: DocFile) => boolean): NavGroup {
  const files = collect(folder);
  return {
    label: folderLabel(folder),
    url: folder.index ? urlFor(folder.index) : null,
    urls: files.map(urlFor),
    pages: files
      .filter(visible)
      .map((file) => (file === folder.index ? link(file, "Overview") : link(file))),
  };
}

/**
 * The navigation for a set of published pages. `home` is the README of docs/; every other top-level
 * file is a loose link, every top-level folder a section, and each folder inside a section a group
 * (deeper folders are listed inside their group, so the sidebar is never more than three levels).
 * A page with `hidden: true` is published and reachable, but is left out of the sidebar and of
 * previous/next.
 */
export function buildNav(
  published: DocFile[],
  siteName = "Documentation",
): { nav: NavData; warnings: string[] } {
  const { files, duplicates } = dedupe(published);
  const warnings = [...duplicates];
  const root = tree(files);
  if (!root.index) {
    throw new Error(
      "docs/README.md is missing: it is the documentation home (/docs/). Add one, or an index.md.",
    );
  }
  const home = root.index;
  const visible = (file: DocFile) => file.data.hidden !== true;
  const sortPages = (pages: DocFile[]) =>
    [...pages].filter(visible).sort(byOrder<DocFile>(orderOf, (f) => labelOf(f)));
  const sortFolders = (folders: Folder[]) => [...folders].sort(byOrder(folderOrder, folderLabel));

  const flat: Array<{ title: string; url: string; file: DocFile; section: string }> = [];
  const push = (file: DocFile, section: string) => {
    if (visible(file))
      flat.push({ title: titleOf(file, siteName), url: urlFor(file), file, section });
  };

  push(home, "");
  const loose = sortPages(root.pages);
  for (const file of loose) push(file, "");

  const sections: NavSection[] = [];
  for (const top of sortFolders(root.folders)) {
    const label = folderLabel(top);
    const own = [...(top.index && visible(top.index) ? [top.index] : []), ...sortPages(top.pages)];
    const groups = sortFolders(top.folders)
      .map((folder) => group(folder, visible))
      .filter((g) => g.pages.length > 0);
    sections.push({
      label,
      url: top.index ? urlFor(top.index) : null,
      urls: collect(top).map(urlFor),
      pages: own.map((file) => (file === top.index ? link(file, "Overview") : link(file))),
      groups,
    });
    for (const file of collect(top)) push(file, label);
  }

  const sectionOf = new Map<string, string>();
  for (const section of sections) for (const url of section.urls) sectionOf.set(url, section.label);
  const order = new Map(flat.map((entry, i) => [entry.url, i]));
  const pages: Record<string, PageInfo> = {};
  for (const file of files) {
    const url = urlFor(file);
    const at = order.get(url);
    const before = at === undefined ? undefined : flat[at - 1];
    const after = at === undefined ? undefined : flat[at + 1];
    pages[url] = {
      title: titleOf(file, siteName),
      description: descriptionOf(file),
      section: sectionOf.get(url) ?? "",
      prev: before ? { title: before.title, url: before.url } : null,
      next: after ? { title: after.title, url: after.url } : null,
      edit: file.rel,
    };
  }

  const nav: NavData = {
    home: {
      label: root.index.data.nav_title ? labelOf(root.index, siteName) : "Overview",
      url: urlFor(home),
    },
    loose: loose.map((file) => link(file)),
    sections: sections.filter((s) => s.pages.length > 0 || s.groups.length > 0),
    expandAll: flat.length - 1 <= EXPAND_ALL_LIMIT,
    pages,
    flat: flat.map(({ title, url }) => ({ title, url })),
    featured: flat.slice(1, 7).map(({ title, url, file, section }) => ({
      title,
      description: descriptionOf(file),
      url,
      section,
    })),
  };
  return { nav, warnings };
}

export { DOCS_PREFIX };
