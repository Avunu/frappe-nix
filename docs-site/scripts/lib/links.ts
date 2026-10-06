// The link checker: reads every built page and proves that what links to something, finds it.
// Pure over a dist/ folder, so the tests run it on small fixtures.
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, posix, relative } from "node:path";
import type { NavData } from "./nav.ts";

export interface Issue {
  page: string;
  message: string;
}

export interface Report {
  pages: number;
  checked: number;
  errors: Issue[];
  warnings: Issue[];
}

const decodeEntities = (value: string) =>
  value
    .replaceAll("&amp;", "&")
    .replaceAll("&quot;", '"')
    .replaceAll("&#39;", "'")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">");

export function htmlPages(dist: string): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const file = join(dir, name);
      if (statSync(file).isDirectory()) walk(file);
      else if (name.endsWith(".html")) out.push(file);
    }
  };
  walk(dist);
  return out.sort();
}

/** The address a built file is served at: dist/docs/a/index.html is /docs/a/, dist/404.html is /404.html. */
export function routeOfFile(dist: string, file: string): string {
  const rel = relative(dist, file).split("\\").join("/");
  return rel.endsWith("index.html") ? `/${rel.slice(0, -"index.html".length)}` : `/${rel}`;
}

/** Markup that can contain text that only looks like links. */
function visibleMarkup(html: string): string {
  return html
    .replaceAll(/<(script|style|template)\b[\s\S]*?<\/\1>/gi, "")
    .replaceAll(/<!--[\s\S]*?-->/g, "");
}

export function idsOf(html: string): Set<string> {
  const ids = new Set<string>();
  for (const m of visibleMarkup(html).matchAll(/\s(?:id|name)=("([^"]*)"|'([^']*)')/g))
    ids.add(decodeEntities(m[2] ?? m[3] ?? ""));
  return ids;
}

interface Reference {
  attribute: "href" | "src";
  value: string;
  tag: string;
}

export function referencesOf(html: string): Reference[] {
  const refs: Reference[] = [];
  for (const m of visibleMarkup(html).matchAll(/<([a-zA-Z][\w-]*)\b([^>]*)>/g)) {
    const tag = m[1]!.toLowerCase();
    for (const a of m[2]!.matchAll(/\s(href|src)=("([^"]*)"|'([^']*)')/g)) {
      refs.push({
        attribute: a[1] as "href" | "src",
        value: decodeEntities(a[3] ?? a[4] ?? ""),
        tag,
      });
    }
  }
  return refs;
}

const EXTERNAL = /^(?:[a-z][a-z0-9+.-]*:|\/\/)/i;

/** Resolves a reference found on `route` to a path (always starting with /) and a fragment. */
export function resolveReference(
  route: string,
  value: string,
): { path: string; hash: string; query: string } | null {
  if (value === "" || EXTERNAL.test(value)) return null;
  const hashAt = value.indexOf("#");
  const hash = hashAt === -1 ? "" : value.slice(hashAt + 1);
  const rest = hashAt === -1 ? value : value.slice(0, hashAt);
  const queryAt = rest.indexOf("?");
  const query = queryAt === -1 ? "" : rest.slice(queryAt);
  const pathPart = queryAt === -1 ? rest : rest.slice(0, queryAt);
  let path: string;
  if (pathPart === "") path = route;
  else if (pathPart.startsWith("/")) path = pathPart;
  else
    path =
      posix.join(route.endsWith("/") ? route : posix.dirname(route) + "/", pathPart) +
      (pathPart.endsWith("/") ? "/" : "");
  try {
    path = decodeURIComponent(path);
  } catch {
    // leave undecodable paths as they are: they will not resolve and are reported
  }
  return {
    path:
      posix.normalize(path) +
      (path.endsWith("/") && !posix.normalize(path).endsWith("/") ? "/" : ""),
    hash: safeDecode(hash),
    query,
  };
}

function safeDecode(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

/** The built file a path is served from, or null. */
export function fileFor(dist: string, path: string): { file: string; exact: boolean } | null {
  const clean = path.replace(/^\//, "");
  if (path.endsWith("/")) {
    const index = join(dist, clean, "index.html");
    return existsSync(index) ? { file: index, exact: true } : null;
  }
  const direct = join(dist, clean);
  if (existsSync(direct) && statSync(direct).isFile()) return { file: direct, exact: true };
  const index = join(dist, clean, "index.html");
  if (existsSync(index)) return { file: index, exact: false };
  return null;
}

export function checkLinks(dist: string, options: { nav?: NavData } = {}): Report {
  const report: Report = { pages: 0, checked: 0, errors: [], warnings: [] };
  const pages = htmlPages(dist);
  report.pages = pages.length;
  const idCache = new Map<string, Set<string>>();
  const idsFor = (file: string) => {
    let ids = idCache.get(file);
    if (!ids) idCache.set(file, (ids = idsOf(readFileSync(file, "utf8"))));
    return ids;
  };
  const err = (page: string, message: string) => report.errors.push({ page, message });
  const warn = (page: string, message: string) => report.warnings.push({ page, message });

  for (const file of pages) {
    const route = routeOfFile(dist, file);
    const html = readFileSync(file, "utf8");
    const visible = visibleMarkup(html);
    const h1 = (visible.match(/<h1[\s>]/g) ?? []).length;
    if (h1 !== 1) err(route, `${h1} <h1> elements (one is required)`);
    if (!/<title>[^<]+<\/title>/.test(html)) err(route, "no <title>");
    if (route !== "/404.html" && !/<meta[^>]*name="description"[^>]*content="[^"]+"/.test(html))
      warn(route, "no meta description");
    // A template expression that was not evaluated at build time would show as literal text. Code
    // samples cannot trigger this: Jx writes a literal "${" in code as "&#36;{".
    const stray = /\$\u200b?\{\s*(?:state|\$map|item|index)\b[^}]*\}/.exec(visible);
    if (stray) err(route, `shows an unevaluated template expression: ${stray[0].slice(0, 60)}`);
    // A callout whose type is not one of the five stays a blockquote with its marker showing.
    const marker = /\[!([A-Za-z]+)\]/.exec(visible.replaceAll(/<(pre|code)\b[\s\S]*?<\/\1>/gi, ""));
    if (marker)
      err(
        route,
        `shows the callout marker ${marker[0]} as text: the types are NOTE, TIP, IMPORTANT, WARNING and CAUTION`,
      );
    const seen = new Set<string>();
    for (const m of visible.matchAll(/\sid=("([^"]*)"|'([^']*)')/g)) {
      const id = decodeEntities(m[2] ?? m[3] ?? "");
      if (seen.has(id)) warn(route, `duplicate id "${id}"`);
      seen.add(id);
    }
    for (const ref of referencesOf(html)) {
      const target = resolveReference(route, ref.value);
      if (!target) continue;
      report.checked++;
      if (/\.md$/i.test(target.path)) {
        err(
          route,
          `${ref.attribute}="${ref.value}" points at a Markdown file; Jx rewrites links only to published pages`,
        );
        continue;
      }
      const found = fileFor(dist, target.path);
      if (!found) {
        err(route, `${ref.attribute}="${ref.value}" does not exist`);
        continue;
      }
      if (!found.exact)
        warn(
          route,
          `${ref.attribute}="${ref.value}" is missing its trailing slash (the host redirects it)`,
        );
      if (target.hash && found.file.endsWith(".html") && !idsFor(found.file).has(target.hash)) {
        err(
          route,
          `${ref.attribute}="${ref.value}": the page has no element with id "${target.hash}"`,
        );
      }
    }
  }

  const index = join(dist, "search-index.json");
  if (existsSync(index)) {
    try {
      const documents =
        (JSON.parse(readFileSync(index, "utf8")) as { documents?: Array<{ url?: string }> })
          .documents ?? [];
      const bad = new Set<string>();
      for (const doc of documents) {
        if (typeof doc.url !== "string") continue;
        report.checked++;
        const target = resolveReference("/", doc.url);
        const found = target ? fileFor(dist, target.path) : null;
        if (!target || !found || (target.hash && !idsFor(found.file).has(target.hash)))
          bad.add(doc.url);
      }
      for (const url of [...bad].slice(0, 20))
        err("/search-index.json", `result address ${url} is not a page or heading`);
      if (bad.size > 20)
        err(
          "/search-index.json",
          `${bad.size - 20} more result addresses are not pages or headings`,
        );
    } catch {
      err("/search-index.json", "is not valid JSON");
    }
  }

  if (options.nav) {
    const urls = new Set<string>([
      options.nav.home.url,
      ...options.nav.flat.map((f) => f.url),
      ...Object.keys(options.nav.pages),
    ]);
    for (const l of options.nav.loose) urls.add(l.url);
    for (const s of options.nav.sections) {
      for (const l of s.pages) urls.add(l.url);
      for (const g of s.groups) for (const l of g.pages) urls.add(l.url);
    }
    for (const url of urls) {
      report.checked++;
      if (!fileFor(dist, url)?.exact)
        err(
          "(sidebar)",
          `the sidebar links to ${url}, which was not built. The address scripts/nav.ts expects is not the one Jx published (see scripts/lib/slug.ts).`,
        );
    }
    for (const file of pages) {
      const route = routeOfFile(dist, file);
      if (route.startsWith("/docs/") && !(route in options.nav.pages))
        err(
          route,
          "was built but is not in the sidebar data (an address scripts/nav.ts did not predict)",
        );
    }
    const home = join(dist, "docs", "index.html");
    if (!existsSync(home))
      err(
        "/docs/",
        "the documentation home was not built: docs/README.md is missing or unpublished",
      );
  }
  return report;
}

export function formatIssues(issues: Issue[]): string {
  return issues.map((i) => `  ${i.page}  ${i.message}`).join("\n");
}

export { dirname };
