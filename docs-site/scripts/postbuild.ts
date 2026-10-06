// Fixes up dist/ after `jx build`:
//   - whitespace: the emitter's separators between inline nodes and between highlighted code tokens
//     (scripts/lib/tidy.ts).
//   - canonical URL and og:url: Jx writes them without the trailing slash the pages are served at.
//   - CNAME: GitHub Pages deployments from Actions read the domain from the repository settings, but
//     the file is kept in the artifact so the domain travels with the site.
//   - 404.html: GitHub Pages serves it for any unknown address (Jx writes it to 404/index.html).
//   - links to repository files that are not pages (LICENSE, source folders) become GitHub links
//     (scripts/lib/repo-links.ts).
//   - sitemap.xml: Jx lists pages without the trailing slash they are served at, lists the 404 page,
//     and stamps every URL with the build time; the sitemap is rewritten from the pages that exist.
//   - search-index.json: a page with no `title` in its frontmatter is indexed under its file name
//     ("README"); the index is given the title the page shows.
//   - index.md: Jx writes a Markdown copy of every page next to it (the whole page, menus and search
//     included, with stray entities); nothing links to them and GitHub Pages would publish them, so
//     they are removed.
//   - code blocks whose language is not highlighted are reported (a warning, not a failure).
//   - <title>: Jx writes the title text as it is, so a `<` in it ("Array<string>") is written raw;
//     it is escaped.
import { existsSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";
import { dirname } from "node:path";
import type { DocsConfig } from "./lib/config.ts";
import type { NavData } from "./lib/nav.ts";
import { type RepoLink, rewriteRepoLinks } from "./lib/repo-links.ts";
import { publishNotFoundPage, tidyPage } from "./lib/tidy.ts";

export interface PostbuildSummary {
  pages: number;
  warnings: string[];
  markdownCopies: number;
  sitemapUrls: number;
  searchTitles: number;
  repoLinks: RepoLink[];
  canonicals: number;
  cname: boolean;
  notFound: boolean;
}

/** Languages that are plain text on purpose: no highlighting is expected for them. */
const PLAIN = new Set(["text", "txt", "plain", "plaintext", "none", "output", "log"]);

/** The languages of the code blocks of a page that were not highlighted (no `shiki` class), once each. */
export function unhighlightedLanguages(html: string): string[] {
  const found = new Set<string>();
  for (const m of html.matchAll(/<code\b[^>]*\bclass="([^"]*)"/g)) {
    const classes = m[1]!.split(/\s+/);
    const language = classes.find((c) => c.startsWith("language-"))?.slice("language-".length);
    if (language && !classes.includes("shiki") && !PLAIN.has(language.toLowerCase()))
      found.add(language);
  }
  return [...found].sort();
}

/** Escapes `<`, `>` and a lone `&` in the text of <title>, which Jx writes unescaped. */
export function escapeTitle(html: string): string {
  return html.replace(
    /<title>([\s\S]*?)<\/title>/,
    (_m, text: string) =>
      `<title>${text
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll(/&(?![a-z][a-z0-9]*;|#\d+;|#x[\da-f]+;)/gi, "&amp;")}</title>`,
  );
}

/** Removes the Markdown copy Jx writes beside each page (`index.md` next to `index.html`). Returns how many. */
export function dropMarkdownCopies(dist: string): number {
  let removed = 0;
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const file = join(dir, name);
      if (statSync(file).isDirectory()) walk(file);
      else if (name === "index.md" && existsSync(join(dir, "index.html"))) {
        rmSync(file);
        removed++;
      }
    }
  };
  walk(dist);
  return removed;
}

export function htmlFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const file = join(dir, name);
    if (statSync(file).isDirectory()) out.push(...htmlFiles(file));
    else if (name.endsWith(".html")) out.push(file);
  }
  return out.sort();
}

/** The address a built file is served at: dist/docs/a/index.html is /docs/a/. */
export function routeOf(dist: string, file: string): string {
  const rel = relative(dist, file).split("\\").join("/");
  if (rel === "404.html") return "/404.html";
  return `/${rel.replace(/index\.html$/, "")}`;
}

function setAttribute(tag: string, attribute: string, value: string): string {
  return tag.replace(new RegExp(`(\\s${attribute}=)(?:"[^"]*"|'[^']*')`), `$1"${value}"`);
}

/** Sets the canonical link and og:url to the served address; other origins are left alone. */
export function fixCanonical(html: string, route: string, site: string): string {
  const want = new URL(route, `${site}/`).href;
  const ours = (value: string | undefined) => !value || value.startsWith(site);
  let out = html.replace(/<link\b[^>]*\brel=["']canonical["'][^>]*>/i, (tag) => {
    const current = /\shref="([^"]*)"/.exec(tag)?.[1];
    return ours(current) ? setAttribute(tag, "href", want) : tag;
  });
  out = out.replace(/<meta\b[^>]*\bproperty=["']og:url["'][^>]*>/i, (tag) => {
    const current = /\scontent="([^"]*)"/.exec(tag)?.[1];
    return ours(current) ? setAttribute(tag, "content", want) : tag;
  });
  return out;
}

const escapeXml = (text: string) =>
  text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");

/** sitemap.xml for the pages that can be indexed: one <loc> per page, at the address it is served at. */
export function sitemapXml(site: string, routes: string[]): string {
  const urls = routes.map(
    (route) => `  <url>\n    <loc>${escapeXml(new URL(route, `${site}/`).href)}</loc>\n  </url>`,
  );
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls.join("\n")}\n</urlset>\n`;
}

/** Whether the page asks search engines to stay away. */
export function isNoindex(html: string): boolean {
  return /<meta\b[^>]*\bname=["']robots["'][^>]*\bcontent=["'][^"']*noindex/i.test(html);
}

interface SearchDocument {
  url?: string;
  title?: string;
  description?: string;
}

/**
 * Gives every document of the search index the title (and, when it has none, the description) of
 * the page it belongs to. `titles` and `descriptions` are keyed by page URL (`/docs/guide/`).
 * Returns the index unchanged, with a count of zero, when it is not the shape Jx writes today.
 */
export function fixSearchIndex(
  raw: string,
  pages: Record<string, { title: string; description: string }>,
): { text: string; changed: number } {
  let index: { documents?: SearchDocument[] };
  try {
    index = JSON.parse(raw) as { documents?: SearchDocument[] };
  } catch {
    return { text: raw, changed: 0 };
  }
  if (!Array.isArray(index.documents)) return { text: raw, changed: 0 };
  let changed = 0;
  for (const doc of index.documents) {
    if (typeof doc.url !== "string") continue;
    const page = pages[doc.url.split("#")[0]!];
    if (!page) continue;
    if (doc.title !== page.title) {
      doc.title = page.title;
      changed++;
    }
    if (!doc.description && page.description) doc.description = page.description;
  }
  return { text: JSON.stringify(index), changed };
}

export interface PostbuildOptions {
  /** The repository root and docs folder, to find the files that docs link to. Omit to skip repository links. */
  repoRoot?: string;
  docsDir?: string;
}

export function runPostbuild(
  dist: string,
  config: Pick<DocsConfig, "domain" | "repo" | "branch">,
  nav?: Pick<NavData, "pages">,
  options: PostbuildOptions = {},
): PostbuildSummary {
  if (!existsSync(dist)) throw new Error(`${dist} does not exist: the build produced nothing`);
  const site = `https://${config.domain}`;
  const notFound = publishNotFoundPage(dist) || existsSync(join(dist, "404.html"));
  const files = htmlFiles(dist);
  let canonicals = 0;
  const repoLinks: RepoLink[] = [];
  const indexable: string[] = [];
  const warnings: string[] = [];
  for (const file of files) {
    const route = routeOf(dist, file);
    const before = readFileSync(file, "utf8");
    let after = escapeTitle(tidyPage(before));
    const plain = unhighlightedLanguages(after);
    if (plain.length > 0) {
      warnings.push(
        `${route}: code in ${plain.join(", ")} is shown without highlighting (the language is not one the build knows; the code itself is unchanged)`,
      );
    }
    const source = nav?.pages[route]?.edit;
    if (source !== undefined && options.repoRoot && options.docsDir) {
      const rewritten = rewriteRepoLinks(
        after,
        route,
        dirname(source) === "." ? "" : dirname(source),
        {
          dist,
          repoRoot: options.repoRoot,
          docsDir: options.docsDir,
          repoUrl: config.repo,
          branch: config.branch ?? "main",
        },
      );
      after = rewritten.html;
      repoLinks.push(...rewritten.links);
    }
    if (route !== "/404.html") {
      const fixed = fixCanonical(after, route, site);
      if (fixed !== after) canonicals++;
      after = fixed;
      if (!isNoindex(after)) indexable.push(route);
    }
    if (after !== before) writeFileSync(file, after);
  }
  writeFileSync(join(dist, "sitemap.xml"), sitemapXml(site, indexable));
  const markdownCopies = dropMarkdownCopies(dist);

  let searchTitles = 0;
  const searchFile = join(dist, "search-index.json");
  if (nav && existsSync(searchFile)) {
    const fixed = fixSearchIndex(readFileSync(searchFile, "utf8"), nav.pages);
    searchTitles = fixed.changed;
    if (fixed.changed > 0) writeFileSync(searchFile, fixed.text);
  }

  const cname = existsSync(join(dist, "CNAME"));
  if (cname && readFileSync(join(dist, "CNAME"), "utf8").trim() !== config.domain) {
    throw new Error(`dist/CNAME does not say ${config.domain}`);
  }
  return {
    pages: files.length,
    warnings,
    markdownCopies,
    sitemapUrls: indexable.length,
    searchTitles,
    repoLinks,
    canonicals,
    cname,
    notFound,
  };
}
