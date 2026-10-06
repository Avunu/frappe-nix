import { afterAll, expect, test } from "bun:test";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { cleanup, makeTree } from "./lib/test-utils.ts";
import {
  dropMarkdownCopies,
  escapeTitle,
  fixCanonical,
  fixSearchIndex,
  isNoindex,
  routeOf,
  runPostbuild,
  sitemapXml,
  unhighlightedLanguages,
} from "./postbuild.ts";

afterAll(cleanup);

const SITE = "https://frappe-nix.avunu.net";
const page = (extra = "") =>
  `<!DOCTYPE html><html><head><title>T</title><link href="${SITE}/docs/a" rel="canonical"><meta content="${SITE}/docs/a" property="og:url">${extra}</head><body><p>x\n</p></body></html>`;

test("routes are the addresses files are served at", () => {
  const dist = "/x/dist";
  expect(routeOf(dist, "/x/dist/index.html")).toBe("/");
  expect(routeOf(dist, "/x/dist/docs/a/index.html")).toBe("/docs/a/");
  expect(routeOf(dist, "/x/dist/404.html")).toBe("/404.html");
});

test("the canonical link and og:url get the trailing slash the page is served at", () => {
  const html = fixCanonical(page(), "/docs/a/", SITE);
  expect(html).toContain(`<link href="${SITE}/docs/a/" rel="canonical">`);
  expect(html).toContain(`<meta content="${SITE}/docs/a/" property="og:url">`);
});

test("a canonical that points at another site is left alone", () => {
  const html = '<link href="https://other.example/x" rel="canonical">';
  expect(fixCanonical(html, "/docs/a/", SITE)).toBe(html);
});

test("the sitemap lists addresses with their trailing slash and nothing else", () => {
  const xml = sitemapXml(SITE, ["/", "/docs/", "/docs/a b/"]);
  expect(xml).toContain("<loc>https://frappe-nix.avunu.net/</loc>");
  expect(xml).toContain("<loc>https://frappe-nix.avunu.net/docs/a%20b/</loc>");
  expect(xml).not.toContain("lastmod");
  expect(xml.startsWith('<?xml version="1.0" encoding="UTF-8"?>')).toBe(true);
});

test("noindex pages are recognised", () => {
  expect(isNoindex('<meta name="robots" content="noindex, nofollow">')).toBe(true);
  expect(isNoindex('<meta name="robots" content="index, follow">')).toBe(false);
  expect(isNoindex("<p>nothing</p>")).toBe(false);
});

test("the search index gets the page's own title, section and heading rows alike", () => {
  const index = JSON.stringify({
    version: 1,
    documents: [
      { url: "/docs/", title: "README", description: "" },
      { url: "/docs/#requirements", title: "README", description: "" },
      { url: "/docs/guide/", title: "Fine", description: "kept" },
      { url: "/elsewhere/", title: "Other" },
    ],
  });
  const { text, changed } = fixSearchIndex(index, {
    "/docs/": { title: "frappe-nix", description: "Nix." },
    "/docs/guide/": { title: "Fine", description: "x" },
  });
  const docs = JSON.parse(text).documents;
  expect(changed).toBe(2);
  expect(docs.map((d: { title: string }) => d.title)).toEqual([
    "frappe-nix",
    "frappe-nix",
    "Fine",
    "Other",
  ]);
  expect(docs[0].description).toBe("Nix.");
  expect(docs[2].description).toBe("kept");
});

test("a search index in a shape it does not know is returned untouched", () => {
  expect(fixSearchIndex("not json", {})).toEqual({ text: "not json", changed: 0 });
  expect(fixSearchIndex('{"other":1}', {})).toEqual({ text: '{"other":1}', changed: 0 });
});

test("runPostbuild tidies pages, rewrites the sitemap, publishes the 404 and fixes the search index", () => {
  const dist = makeTree({
    "index.html": page().replace("/docs/a", ""),
    "docs/a/index.html": page("<p>Run \n<code>x</code>\n.</p>"),
    "404/index.html":
      '<title>Not found</title><meta name="robots" content="noindex, nofollow"><h1>x</h1>',
    CNAME: "frappe-nix.avunu.net\n",
    "sitemap.xml": "<old/>",
    "search-index.json": JSON.stringify({ documents: [{ url: "/docs/a/", title: "a" }] }),
  });
  const summary = runPostbuild(
    dist,
    { domain: "frappe-nix.avunu.net", repo: "https://github.com/Avunu/frappe-nix" },
    {
      pages: {
        "/docs/a/": {
          title: "Page A",
          description: "",
          section: "",
          prev: null,
          next: null,
          edit: "a.md",
        },
      },
    },
  );
  expect(summary).toMatchObject({
    pages: 3,
    sitemapUrls: 2,
    searchTitles: 1,
    notFound: true,
    cname: true,
  });
  expect(existsSync(join(dist, "404.html"))).toBe(true);
  expect(readFileSync(join(dist, "docs/a/index.html"), "utf8")).toContain(
    "<p>Run <code>x</code>.</p>",
  );
  const sitemap = readFileSync(join(dist, "sitemap.xml"), "utf8");
  expect(sitemap).toContain("/docs/a/</loc>");
  expect(sitemap).not.toContain("404");
  expect(JSON.parse(readFileSync(join(dist, "search-index.json"), "utf8")).documents[0].title).toBe(
    "Page A",
  );
});

test("a CNAME that does not match the configured domain is an error", () => {
  const dist = makeTree({ "index.html": page(), CNAME: "someone-else.avunu.net\n" });
  expect(() =>
    runPostbuild(dist, { domain: "frappe-nix.avunu.net", repo: "https://github.com/a/b" }),
  ).toThrow(/CNAME/);
  expect(() =>
    runPostbuild(join(dist, "missing"), { domain: "x.avunu.net", repo: "https://github.com/a/b" }),
  ).toThrow(/does not exist/);
});

test("repository links are rewritten from the docs file that holds them", () => {
  const repo = makeTree({
    LICENSE: "MIT",
    "lib/tool.nix": "x",
    "docs/README.md": "# Home\n",
  });
  const dist = join(repo, "docs-site", "dist");
  mkdirSync(join(dist, "docs"), { recursive: true });
  writeFileSync(
    join(dist, "docs", "index.html"),
    page(
      '<a href="lib/tool.nix">tool</a><a href="LICENSE#top">license</a><a href="missing.txt">gone</a>',
    ),
  );
  const summary = runPostbuild(
    dist,
    {
      domain: "frappe-nix.avunu.net",
      repo: "https://github.com/Avunu/frappe-nix",
      branch: "trunk",
    },
    {
      pages: {
        "/docs/": {
          title: "Home",
          description: "",
          section: "",
          prev: null,
          next: null,
          edit: "README.md",
        },
      },
    },
    { repoRoot: repo, docsDir: join(repo, "docs") },
  );
  expect(summary.repoLinks.map((l) => l.to)).toEqual([
    "https://github.com/Avunu/frappe-nix/blob/trunk/lib/tool.nix",
    "https://github.com/Avunu/frappe-nix/blob/trunk/LICENSE#top",
  ]);
  const html = readFileSync(join(dist, "docs", "index.html"), "utf8");
  expect(html).toContain('href="https://github.com/Avunu/frappe-nix/blob/trunk/lib/tool.nix"');
  expect(html).toContain('href="missing.txt"');
});

test("the text of <title> is escaped, entities that are already there are kept", () => {
  expect(escapeTitle("<title>Array<string> & more</title>")).toBe(
    "<title>Array&lt;string&gt; &amp; more</title>",
  );
  expect(escapeTitle("<title>Tom &amp; Jerry &#38; &copy;</title><p>a < b</p>")).toBe(
    "<title>Tom &amp; Jerry &#38; &copy;</title><p>a < b</p>",
  );
  expect(escapeTitle("<p>no title</p>")).toBe("<p>no title</p>");
});

test("code blocks in a language the build cannot highlight are reported once each", () => {
  const html =
    '<pre><code class="language-bash shiki">x</code></pre><pre><code class="language-rust">y</code></pre><pre><code class="language-rust">z</code></pre><pre><code class="language-vue">v</code></pre><pre><code class="language-text">t</code></pre><pre><code>no language</code></pre>';
  expect(unhighlightedLanguages(html)).toEqual(["rust", "vue"]);
  expect(unhighlightedLanguages("<p>none</p>")).toEqual([]);
});

test("the Markdown copies Jx writes beside pages are removed, other Markdown files are not", () => {
  const dist = makeTree({
    "index.html": "<p>x</p>",
    "index.md": "# copy",
    "docs/a/index.html": "<p>a</p>",
    "docs/a/index.md": "# copy",
    "docs/lonely/index.md": "# no page next to it",
    "downloads/notes.md": "# a real file",
  });
  expect(dropMarkdownCopies(dist)).toBe(2);
  expect(existsSync(join(dist, "index.md"))).toBe(false);
  expect(existsSync(join(dist, "docs", "a", "index.md"))).toBe(false);
  expect(existsSync(join(dist, "docs", "lonely", "index.md"))).toBe(true);
  expect(existsSync(join(dist, "downloads", "notes.md"))).toBe(true);
});

test("runPostbuild reports unhighlighted languages as warnings and escapes the title", () => {
  const dist = makeTree({
    "docs/a/index.html": page('<pre><code class="language-rust">x</code></pre>').replace(
      "<title>T</title>",
      "<title>A<b></title>",
    ),
    "docs/a/index.md": "# copy",
  });
  const summary = runPostbuild(dist, {
    domain: "frappe-nix.avunu.net",
    repo: "https://github.com/a/b",
  });
  expect(summary.warnings).toHaveLength(1);
  expect(summary.warnings[0]).toContain("/docs/a/");
  expect(summary.warnings[0]).toContain("rust");
  expect(summary.markdownCopies).toBe(1);
  expect(readFileSync(join(dist, "docs", "a", "index.html"), "utf8")).toContain(
    "<title>A&lt;b&gt;</title>",
  );
});
