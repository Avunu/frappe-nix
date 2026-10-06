import { afterAll, expect, test } from "bun:test";
import { cleanup, makeTree } from "./lib/test-utils.ts";
import { checkLinks, referencesOf, resolveReference, routeOfFile } from "./lib/links.ts";
import type { NavData } from "./lib/nav.ts";

afterAll(cleanup);

const page = (body: string, title = "T") =>
  `<!DOCTYPE html><html><head><title>${title}</title><meta name="description" content="d"></head><body>${body}</body></html>`;
const fixture = (extra: Record<string, string> = {}) =>
  makeTree({
    "index.html": page('<h1>Home</h1><a href="/docs/">docs</a>'),
    "docs/index.html": page(
      '<h1 id="home">Home</h1><h2 id="sec">S</h2><a href="getting-started/">gs</a> <a href="#sec">self</a> <a href="/docs/getting-started/#install">deep</a><img src="/brand/x.svg" alt="">',
    ),
    "docs/getting-started/index.html": page(
      '<h1>GS</h1><h2 id="install">I</h2><a href="../">up</a>',
    ),
    "brand/x.svg": "<svg/>",
    ...extra,
  });

test("references are read from href and src, and not from scripts or comments", () => {
  const refs = referencesOf(
    '<a href="/a/">a</a><img src="b.png"><script>var x = \'<a href="/not/">\'</script><!-- <a href="/no/"> --><link href="/c.css" rel="stylesheet">',
  );
  expect(refs.map((r) => r.value)).toEqual(["/a/", "b.png", "/c.css"]);
});

test("references resolve against the page they are on", () => {
  expect(resolveReference("/docs/a/", "../b/")).toEqual({ path: "/docs/b/", hash: "", query: "" });
  expect(resolveReference("/docs/a/", "c/#x%20y")).toEqual({
    path: "/docs/a/c/",
    hash: "x y",
    query: "",
  });
  expect(resolveReference("/docs/a/", "#top")).toEqual({
    path: "/docs/a/",
    hash: "top",
    query: "",
  });
  expect(resolveReference("/docs/a/", "/x/y.png?v=1")).toEqual({
    path: "/x/y.png",
    hash: "",
    query: "?v=1",
  });
  expect(resolveReference("/docs/a/", "https://example.com")).toBeNull();
  expect(resolveReference("/docs/a/", "mailto:x@y.z")).toBeNull();
  expect(resolveReference("/docs/a/", "//cdn.example/x")).toBeNull();
});

test("files map to the addresses they are served at", () => {
  expect(routeOfFile("/d", "/d/index.html")).toBe("/");
  expect(routeOfFile("/d", "/d/docs/a/index.html")).toBe("/docs/a/");
  expect(routeOfFile("/d", "/d/404.html")).toBe("/404.html");
});

test("a healthy site has no errors", () => {
  const report = checkLinks(fixture());
  expect(report.errors).toEqual([]);
  expect(report.pages).toBe(3);
  expect(report.checked).toBeGreaterThan(5);
});

test("a missing page, a missing anchor and a link to a Markdown file are errors", () => {
  const dist = fixture({
    "docs/bad/index.html": page(
      '<h1>B</h1><a href="/docs/nope/">1</a><a href="/docs/#missing">2</a><a href="other.md">3</a><img src="/gone.png" alt="">',
    ),
  });
  const messages = checkLinks(dist).errors.map((e) => e.message);
  expect(messages.some((m) => m.includes('"/docs/nope/" does not exist'))).toBe(true);
  expect(messages.some((m) => m.includes('no element with id "missing"'))).toBe(true);
  expect(messages.some((m) => m.includes("Markdown file"))).toBe(true);
  expect(messages.some((m) => m.includes("/gone.png"))).toBe(true);
});

test("a link without the trailing slash works but is a warning", () => {
  const report = checkLinks(
    fixture({ "docs/x/index.html": page('<h1>X</h1><a href="/docs/getting-started">gs</a>') }),
  );
  expect(report.errors).toEqual([]);
  expect(report.warnings.some((w) => w.message.includes("trailing slash"))).toBe(true);
});

test("every page needs exactly one h1 and a title", () => {
  const report = checkLinks(
    fixture({
      "docs/two/index.html": page("<h1>a</h1><h1>b</h1>"),
      "docs/none/index.html": "<html><body><p>x</p></body></html>",
    }),
  );
  const messages = report.errors.map((e) => `${e.page} ${e.message}`);
  expect(messages.some((m) => m.includes("/docs/two/") && m.includes("2 <h1>"))).toBe(true);
  expect(messages.some((m) => m.includes("/docs/none/") && m.includes("0 <h1>"))).toBe(true);
  expect(messages.some((m) => m.includes("/docs/none/") && m.includes("no <title>"))).toBe(true);
});

test("a template expression that reached the page unevaluated is an error, escaped code is not", () => {
  const dist = fixture({
    "docs/stray/index.html": page('<h1>S</h1><a href="/">${state.config.data.name} home</a>'),
    "docs/code/index.html": page(
      "<h1>C</h1><pre><code>echo &#36;{state.x} and ${HOME}</code></pre>",
    ),
  });
  const messages = checkLinks(dist).errors.map((e) => `${e.page} ${e.message}`);
  expect(messages.some((m) => m.includes("/docs/stray/") && m.includes("unevaluated"))).toBe(true);
  expect(messages.some((m) => m.includes("/docs/code/"))).toBe(false);
});

test("search results must be pages or headings that exist", () => {
  const good = JSON.stringify({
    documents: [
      { url: "/docs/" },
      { url: "/docs/#sec" },
      { url: "/docs/getting-started/#install" },
    ],
  });
  expect(checkLinks(fixture({ "search-index.json": good })).errors).toEqual([]);
  const bad = JSON.stringify({ documents: [{ url: "/docs/#nope" }, { url: "/docs/missing/" }] });
  expect(checkLinks(fixture({ "search-index.json": bad })).errors).toHaveLength(2);
});

test("every sidebar entry must have been built, and every built docs page must be in the sidebar", () => {
  const link = (url: string) => ({ label: url, url });
  const nav = {
    home: link("/docs/"),
    loose: [link("/docs/getting-started/")],
    sections: [{ label: "G", url: null, urls: [], pages: [link("/docs/ghost/")], groups: [] }],
    expandAll: true,
    pages: { "/docs/": {}, "/docs/getting-started/": {}, "/docs/ghost/": {} },
    flat: [],
    featured: [],
  } as unknown as NavData;
  const report = checkLinks(fixture({ "docs/extra/index.html": page("<h1>E</h1>") }), { nav });
  const messages = report.errors.map((e) => e.message);
  expect(messages.some((m) => m.includes("/docs/ghost/"))).toBe(true);
  expect(messages.some((m) => m.includes("not in the sidebar data"))).toBe(true);
});

test("a callout marker that stayed text is an error, one in code is not", () => {
  const dist = fixture({
    "docs/marker/index.html": page("<h1>M</h1><blockquote><p>[!QUESTION] why?</p></blockquote>"),
    "docs/code/index.html": page(
      "<h1>C</h1><pre><code>&gt; [!NOTE]</code></pre><p><code>[!TIP]</code></p>",
    ),
  });
  const messages = checkLinks(dist).errors.map((e) => `${e.page} ${e.message}`);
  expect(messages.some((m) => m.includes("/docs/marker/") && m.includes("[!QUESTION]"))).toBe(true);
  expect(messages.some((m) => m.includes("/docs/code/"))).toBe(false);
});
