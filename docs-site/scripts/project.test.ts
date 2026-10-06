// Checks on the project's own JSON: every document parses, every component is registered under its
// own tag, every design token that is used is defined, no component carries a raw colour, the
// JavaScript in state entries is valid, and the project switcher's logic (which lives twice, once at
// build time in the base layout and once in the browser in the component) is the same text and gives
// the right groups.
import { expect, test } from "bun:test";
import { readFileSync, readdirSync } from "node:fs";
import { basename, join } from "node:path";
import { ROOT } from "./lib/config.ts";
import { contrastFailures, highlightOf, ratio } from "./check-contrast.ts";

const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const json = (path: string) => JSON.parse(read(path)) as Record<string, any>;
const files = (dir: string) =>
  readdirSync(join(ROOT, dir))
    .filter((f) => f.endsWith(".json"))
    .sort()
    .map((f) => `${dir}/${f}`);

const project = json("project.json");
const components = files("components");
const layouts = files("layouts");
const pages = files("pages");

test("every project document is valid JSON", () => {
  for (const file of [
    "project.json",
    "docs.config.json",
    "data/projects.snapshot.json",
    "package.json",
    ...components,
    ...layouts,
    ...pages,
  ]) {
    expect(() => JSON.parse(read(file)), file).not.toThrow();
  }
});

test("a component's file name is its tag, and tags and ids are unique", () => {
  const tags = new Set<string>();
  const ids = new Set<string>();
  for (const file of components) {
    const component = json(file);
    const tag = String(component.tagName);
    expect(basename(file, ".json"), file).toBe(tag);
    expect(tag, file).toContain("-");
    expect(tags.has(tag), `${file} repeats ${tag}`).toBe(false);
    tags.add(tag);
    expect(typeof component.$id, file).toBe("string");
    expect(ids.has(component.$id), `${file} repeats ${component.$id}`).toBe(false);
    ids.add(component.$id);
  }
});

/** Every `--name` that is defined (as a key) or used (inside var()) in a JSON document. */
function tokens(value: unknown, defined = new Set<string>(), used = new Set<string>()) {
  if (typeof value === "string") {
    for (const m of value.matchAll(/var\((--[\w-]+)/g)) used.add(m[1]!);
  } else if (Array.isArray(value)) {
    for (const v of value) tokens(v, defined, used);
  } else if (value && typeof value === "object") {
    for (const [k, v] of Object.entries(value)) {
      if (k.startsWith("--")) defined.add(k);
      tokens(v, defined, used);
    }
  }
  return { defined, used };
}

test("every design token that is used is defined, and the dark theme only overrides tokens that exist", () => {
  const defined = new Set<string>(Object.keys(project.style).filter((k) => k.startsWith("--")));
  const own = new Set<string>();
  const used = new Set<string>();
  for (const file of ["project.json", ...components, ...layouts, ...pages]) {
    const t = tokens(json(file));
    t.used.forEach((u) => used.add(u));
    if (file !== "project.json") t.defined.forEach((d) => own.add(d)); // a component may define a local property
  }
  // --shiki-light and --shiki-dark are set by the code highlighter on each token, not by the project.
  const missing = [...used].filter(
    (u) => !defined.has(u) && !own.has(u) && !u.startsWith("--shiki-"),
  );
  expect(missing).toEqual([]);
  for (const name of Object.keys(project.style["@--dark"]))
    expect(defined.has(name), `${name} is overridden in dark but not defined`).toBe(true);
});

test("components, layouts and pages use tokens, never raw hex colours", () => {
  for (const file of [...components, ...layouts, ...pages]) {
    const hex =
      read(file)
        .match(/#[0-9a-fA-F]{3,8}\b/g)
        ?.filter((h) => !/^#[0-9a-fA-F]{1,2}$/.test(h)) ?? [];
    // Strings such as "#main" or "#${...}" are anchors, not colours: a colour is 3, 4, 6 or 8 hex digits.
    const colours = hex.filter((h) => [4, 5, 7, 9].includes(h.length) && /^#[0-9a-fA-F]+$/.test(h));
    const real = colours.filter((c) => !["#main", "#top"].includes(c));
    expect(real, file).toEqual([]);
  }
});

/** Every inline JavaScript body in a document: Function bodies and handlers. */
function bodies(
  value: unknown,
  found: Array<{ where: string; body: string }> = [],
  where = "",
): Array<{ where: string; body: string }> {
  if (Array.isArray(value)) value.forEach((v, i) => bodies(v, found, `${where}[${i}]`));
  else if (value && typeof value === "object") {
    const o = value as Record<string, unknown>;
    if (o.$prototype === "Function" && typeof o.body === "string")
      found.push({ where, body: o.body });
    for (const [k, v] of Object.entries(o)) bodies(v, found, `${where}.${k}`);
  }
  return found;
}

test("every Function body is valid JavaScript, and a compiler-timed one has no sidecar", () => {
  let count = 0;
  for (const file of [...components, ...layouts, ...pages]) {
    for (const { where, body } of bodies(json(file))) {
      count++;
      expect(
        () => new Function("state", `return (async function () { ${body}\n}).call(this)`),
        `${file} ${where}`,
      ).not.toThrow();
    }
  }
  expect(count).toBeGreaterThan(15);
  for (const file of [...layouts, ...pages]) {
    for (const [key, entry] of Object.entries((json(file).state ?? {}) as Record<string, any>)) {
      if (entry?.timing === "compiler" && entry.$prototype === "Function")
        expect(entry.$src, `${file} state.${key}`).toBeUndefined();
    }
  }
});

test("every layout and page derives its values at build time, so pages ship no JavaScript of their own", () => {
  for (const file of [...layouts, ...pages]) {
    for (const [key, entry] of Object.entries((json(file).state ?? {}) as Record<string, any>)) {
      const ok = typeof entry === "object" && entry !== null && entry.timing === "compiler";
      expect(ok, `${file} state.${key} must be compiler-timed`).toBe(true);
    }
  }
});

// ── the project switcher ────────────────────────────────────────────────────────────────────────
const between = (text: string) => {
  const a = text.indexOf("// switcher:start");
  const b = text.indexOf("// switcher:end");
  expect(a).toBeGreaterThan(-1);
  expect(b).toBeGreaterThan(a);
  return text.slice(a, b + "// switcher:end".length);
};
const layoutBody: string = json("layouts/base.json").state.switcherGroups.body;
const componentBody: string = json("components/project-switcher.json").state.onMount.body;

test("the switcher's grouping logic is the same text at build time and in the browser", () => {
  expect(between(componentBody)).toBe(between(layoutBody));
});

const catalog = (projects: unknown[]) => ({
  version: 1,
  generated: "2026-10-05T00:00:00Z",
  site: "https://avunu.net",
  projects,
});
const entry = (slug: string, platform: string, extra: Record<string, unknown> = {}) => ({
  slug,
  title: slug.toUpperCase(),
  platform,
  summary: "s",
  repo: `https://github.com/Avunu/${slug}`,
  page: `https://avunu.net/open-source/${slug}/`,
  docs: null,
  license: "MIT",
  status: "active",
  suite: null,
  ...extra,
});
const groups = (data: unknown, current: string) =>
  new Function("state", layoutBody)({
    catalog: { data },
    config: { data: { slug: current } },
  }) as Array<{ platform: string; label: string; items: Array<Record<string, any>> }>;

test("projects are grouped by platform in the fixed order, sorted by title, with the current one marked", () => {
  const result = groups(
    catalog([
      entry("b", "odoo"),
      entry("z", "nixos"),
      entry("a", "frappe"),
      entry("c", "odoo"),
      entry("m", "mystery"),
    ]),
    "z",
  );
  expect(result.map((g) => g.label)).toEqual(["Frappe & ERPNext", "Odoo", "NixOS", "mystery"]);
  expect(result[1]!.items.map((i) => i.title)).toEqual(["B", "C"]);
  const current = result.flatMap((g) => g.items).filter((i) => i.current);
  expect(current).toHaveLength(1);
  expect(current[0]).toMatchObject({ slug: "z", caption: "You are here" });
});

test("an entry links to its docs site when it has one, else to its avunu.net page", () => {
  const items = groups(
    catalog([entry("a", "frappe", { docs: "https://a.avunu.net" }), entry("b", "frappe")]),
    "none",
  )[0]!.items;
  expect(items[0]).toMatchObject({ href: "https://a.avunu.net", caption: "Docs" });
  expect(items[1]).toMatchObject({
    href: "https://avunu.net/open-source/b/",
    caption: "avunu.net",
  });
});

test("malformed entries and non-https links are dropped", () => {
  const result = groups(
    catalog([
      entry("ok", "frappe"),
      { slug: "x" },
      entry("bad", "frappe", { page: "javascript:alert(1)" }),
      entry("worse", "frappe", { page: "http://insecure.example", docs: "javascript:1" }),
      null,
    ]),
    "none",
  );
  expect(result.flatMap((g) => g.items.map((i) => i.slug))).toEqual(["ok"]);
  expect(groups({ ...catalog([]), version: 2 }, "none")).toEqual([]);
  expect(groups(null, "none")).toEqual([]);
});

test("a catalog with every platform builds every group, from a fixed fixture", () => {
  const fixture = catalog([
    entry("a", "frappe"),
    entry("b", "odoo"),
    entry("c", "wordpress"),
    entry("d", "nixos"),
    entry("e", "general"),
  ]);
  const result = groups(fixture, "d");
  expect(result.map((g) => g.platform)).toEqual([
    "frappe",
    "odoo",
    "wordpress",
    "nixos",
    "general",
  ]);
  expect(result.flatMap((g) => g.items)).toHaveLength(5);
});

test("whatever the live catalog holds, the committed snapshot builds into groups without a throw", () => {
  // The workflow refreshes the snapshot from avunu.net before the tests: it may have one project or
  // one platform, and the tests must not care. Every entry of a valid catalog is listed once.
  const snapshot = JSON.parse(read("data/projects.snapshot.json"));
  const result = groups(snapshot, "frappe-nix");
  expect(result.flatMap((g) => g.items)).toHaveLength(snapshot.projects.length);
  for (const one of [
    catalog([entry("only", "nixos")]),
    catalog([entry("a", "odoo"), entry("b", "odoo")]),
  ]) {
    const built = groups(one, "none");
    expect(built.flatMap((g) => g.items).length).toBe(one.projects.length);
  }
});

test("a bad entry never discards the others, and the catalog cannot flood or confuse the list", () => {
  const result = groups(
    catalog([
      entry("ok", "frappe"),
      entry("__proto__", "constructor"),
      entry("constructor", "frappe"),
      entry("dup", "frappe", { title: "First" }),
      entry("dup", "frappe", { title: "Second" }),
      entry("blank", "frappe", { title: "   " }),
      entry("long", "frappe", { title: "x".repeat(500) }),
      entry("evil", "frappe", { page: "https://evil.example/phish", docs: null }),
      entry("userinfo", "frappe", { page: "https://avunu.net@evil.example/" }),
      {
        slug: "throws",
        title: "T",
        platform: "frappe",
        get page(): string {
          throw new Error("boom");
        },
      },
      entry("late", "nixos"),
    ]),
    "ok",
  );
  const items = result.flatMap((g) => g.items);
  expect(items.map((i) => i.slug).sort()).toEqual(
    ["__proto__", "constructor", "dup", "evil", "late", "long", "ok", "userinfo"].sort(),
  );
  expect(items.find((i) => i.slug === "dup")!.title).toBe("First");
  expect(items.find((i) => i.slug === "long")!.title).toHaveLength(120);
  expect(items.filter((i) => i.current)).toHaveLength(1);
  // The caption names the host the link goes to, whatever it is.
  expect(items.find((i) => i.slug === "evil")!.caption).toBe("evil.example");
  expect(items.find((i) => i.slug === "userinfo")!.caption).toBe("evil.example");
  // A platform with a name that is a property of Object does not break the groups.
  expect(result.map((g) => g.label)).toContain("constructor");
  const many = groups(
    catalog(Array.from({ length: 1000 }, (_v, i) => entry(`p${i}`, "frappe"))),
    "none",
  );
  expect(many.flatMap((g) => g.items)).toHaveLength(300);
});

// ── design tokens ───────────────────────────────────────────────────────────────────────────────
const searchStyle = json("components/docs-search.json").style as Record<
  string,
  Record<string, unknown>
>;

test("the colour pairs that are listed, and the search highlight read from its component, meet WCAG AA in both themes", () => {
  const highlight = highlightOf(searchStyle["& .hl"]);
  expect(highlight).toEqual({ token: "--color-action", percent: 22, text: ["--color-text"] });
  const { checked, failures } = contrastFailures(project.style, highlight);
  expect(checked).toBeGreaterThan(140);
  expect(failures).toEqual([]);
});

test("the contrast check fails on a highlight that is too weak a mix for the text on it", () => {
  // Text in the text colour on a highlight of the action colour at 100 percent: below 4.5 in light.
  const strong = contrastFailures(project.style, {
    token: "--color-action",
    percent: 100,
    text: ["--color-text"],
  });
  expect(strong.failures.some((f) => f.label.startsWith("search highlight"))).toBe(true);
  // The highlight as it was shipped first (the text colour inherited, so the caption colour of an
  // excerpt) fails in the light theme: 3.6:1 on the lavender, against 4.5:1.
  const inherited = highlightOf({
    backgroundColor: "color-mix(in srgb, var(--color-action) 22%, transparent)",
    color: "inherit",
  });
  expect(inherited!.text).toEqual(["--color-text", "--color-text-caption"]);
  expect(
    contrastFailures(project.style, inherited).failures.some((f) => f.label.includes("caption")),
  ).toBe(true);
  expect(highlightOf({ backgroundColor: "#ff0000" })).toBeNull();
  expect(highlightOf(undefined)).toBeNull();
});

test("the browser's theme-color meta tags are the page colours of the two themes", () => {
  const metas = (project.$head as Array<Record<string, any>>).filter(
    (h) => h.attributes?.name === "theme-color",
  );
  const byMedia = Object.fromEntries(metas.map((m) => [m.attributes.media, m.attributes.content]));
  expect(byMedia["(prefers-color-scheme: light)"]).toBe(project.style["--color-bg"]);
  expect(byMedia["(prefers-color-scheme: dark)"]).toBe(project.style["@--dark"]["--color-bg"]);
});

test("the contrast maths: black on white is 21:1, a colour on itself 1:1", () => {
  expect(Math.round(ratio("#000000", "#FFFFFF", "#FFFFFF"))).toBe(21);
  expect(ratio("#6237BF", "#6237BF", "#FFFFFF")).toBeCloseTo(1, 5);
  expect(ratio("rgba(0, 0, 0, 0.5)", "#FFFFFF", "#FFFFFF")).toBeGreaterThan(3);
});

test("the content type mirrors what scripts/lib/docs.ts assumes", () => {
  const docs = project.content.docs;
  expect(docs.source).toBe("./.generated/docs");
  expect(docs.route).toBe("/docs/{id:slug}/");
  expect(docs.indexRoute).toBe("/docs/{dir:slug}/");
  expect(docs.where).toEqual({ draft: { $ne: true }, publish: { $ne: false } });
  expect(docs.exclude).toEqual(["**/node_modules/**", "**/.*", "**/.*/**", "**/_*", "**/_*/**"]);
  expect(Object.keys(docs.alerts).sort()).toEqual([
    "CAUTION",
    "IMPORTANT",
    "NOTE",
    "TIP",
    "WARNING",
  ]);
  expect(project.search.collections.docs.basePath).toBe("/docs/");
});

// ── semantics of the interactive parts ──────────────────────────────────────────────────────────
/** Every node of a component document that satisfies `match`. */
function nodes(
  value: unknown,
  match: (n: Record<string, any>) => boolean,
  found: Array<Record<string, any>> = [],
) {
  if (Array.isArray(value)) value.forEach((v) => nodes(v, match, found));
  else if (value && typeof value === "object") {
    if (match(value as Record<string, any>)) found.push(value as Record<string, any>);
    Object.values(value).forEach((v) => nodes(v, match, found));
  }
  return found;
}

test("the search listbox holds options and nothing else; the status and the links sit beside it", () => {
  const search = json("components/docs-search.json");
  const [listbox] = nodes(search, (n) => n.attributes?.role === "listbox");
  expect(listbox).toBeDefined();
  const content = listbox!.children as Array<Record<string, any>>;
  expect(content).toHaveLength(1);
  expect(content[0]!.$prototype).toBe("Array");
  expect(content[0]!.map.attributes.role).toBe("option");
  // Nothing that is not an option is inside it (the empty-state text, the "Jump to" links).
  expect(nodes(listbox, (n) => n.attributes?.role === "status")).toEqual([]);
  expect(nodes(listbox, (n) => n.attributes?.class === "quick-link")).toEqual([]);
  expect(nodes(search, (n) => n.attributes?.role === "status")).toHaveLength(1);
  expect(nodes(search, (n) => n.attributes?.class === "quick-link")).toHaveLength(1);
  // An empty list is hidden rather than left as an empty listbox.
  expect(listbox!.attributes.hidden).toContain("searchResults.length === 0");
});

test("the project switcher is a disclosure of links, not a menu", () => {
  const switcher = json("components/project-switcher.json");
  expect(
    nodes(switcher, (n) => /^(menu|menuitem|menubar)$/.test(String(n.attributes?.role))),
  ).toEqual([]);
  const [button] = nodes(switcher, (n) => n.tagName === "button");
  expect(button!.attributes["aria-haspopup"]).toBeUndefined();
  expect(button!.attributes["aria-expanded"]).toBe("false");
  expect(button!.attributes.popovertarget).toBe("project-switcher-menu");
  const links = nodes(switcher, (n) => n.tagName === "a");
  expect(links.length).toBeGreaterThanOrEqual(3);
  // A link is named "Title, Docs": its title, then where it goes, with a separator a reader hears.
  for (const item of nodes(switcher, (n) => n.tagName === "li")) {
    const link = item.children[0];
    expect(link.attributes["aria-label"]).toMatch(/^.+, .+$/);
    expect(link.children.map((c: any) => c.attributes.class)).toEqual(["t", "c"]);
  }
  // The keyboard code of a menu is gone with the roles.
  const body = switcher.state.onMount.body as string;
  expect(body).not.toContain("ArrowDown");
  expect(body).not.toContain("menuitem");
  expect(body).toContain("focusout");
});

test("the documentation drawer is made modal while it is open", () => {
  const enhance = json("components/docs-enhance.json").state.onMount.body as string;
  for (const part of ["docs-drawer", "inert", "aria-expanded", "aria-modal", ".drawer-close"])
    expect(enhance, part).toContain(part);
  const [opener] = nodes(json("layouts/docs.json"), (n) => n.attributes?.class === "menu-btn");
  expect(opener!.attributes["aria-expanded"]).toBe("false");
  expect(opener!.attributes.popovertarget).toBe("docs-drawer");
});
