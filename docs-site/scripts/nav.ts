// Writes .generated/nav.json: the sidebar order, previous/next links and landing-page cards, from
// the folder structure of docs/ and the frontmatter `order` of each page.
//
//   bun scripts/nav.ts [--docs <folder>] [--out .generated/nav.json] [--print] [--watch]
//
// `bun run build` and `bun run dev` run it first. Sidebar order: pages sort by `order` (a number in
// the frontmatter; pages without one come last) and then by title. A folder is a section; its
// README.md (or index.md) is the section's "Overview" and its `order` places the section.
import { mkdirSync, watch, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { type DocsConfig, ROOT, docsDir, readConfig, stagedDocsDir } from "./lib/config.ts";
import { readDocs } from "./lib/docs.ts";
import { buildNav, type NavData } from "./lib/nav.ts";
import { stageSite } from "./lib/stage.ts";

export function generateNav(
  dir: string,
  siteName?: string,
): { nav: NavData; warnings: string[]; pages: number } {
  const files = readDocs(dir);
  const { nav, warnings } = buildNav(files, siteName);
  return { nav, warnings, pages: files.length };
}

export function writeNav(
  dir: string,
  out: string,
  siteName?: string,
): { pages: number; warnings: string[]; nav: NavData } {
  const { nav, warnings, pages } = generateNav(dir, siteName);
  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, `${JSON.stringify(nav, null, 2)}\n`);
  return { pages, warnings, nav };
}

function printTree(nav: NavData): void {
  const line = (depth: number, text: string) => console.log(`${"  ".repeat(depth)}${text}`);
  line(0, `${nav.home.label}  ${nav.home.url}`);
  for (const page of nav.loose) line(0, `${page.label}  ${page.url}`);
  for (const section of nav.sections) {
    line(0, `${section.label}/${section.url ? `  ${section.url}` : ""}`);
    for (const page of section.pages) line(1, `${page.label}  ${page.url}`);
    for (const group of section.groups) {
      line(1, `${group.label}/`);
      for (const page of group.pages) line(2, `${page.label}  ${page.url}`);
    }
  }
}

if (import.meta.main) {
  const args = process.argv.slice(2);
  const option = (name: string) => {
    const at = args.indexOf(name);
    return at === -1 ? undefined : args[at + 1];
  };
  // Without --docs the sidebar is made from the staged copy of docs/ (what Jx reads), which this
  // command refreshes first.
  const given = option("--docs");
  const watched = given ? resolve(ROOT, given) : docsDir(ROOT);
  const out = resolve(ROOT, option("--out") ?? ".generated/nav.json");
  let config: Pick<DocsConfig, "name" | "repo" | "branch"> | undefined;
  try {
    config = readConfig(ROOT);
  } catch {
    config = undefined;
  }
  const run = () => {
    try {
      if (!given && config) stageSite(ROOT, config);
      const docs = given ? watched : stagedDocsDir(ROOT);
      const { pages, warnings } = writeNav(docs, out, config?.name);
      for (const warning of warnings) console.warn(`nav: warning: ${warning}`);
      console.log(`nav: ${pages} page(s) -> ${out.replace(`${ROOT}/`, "")}`);
      if (args.includes("--print")) printTree(generateNav(docs, config?.name).nav);
      return true;
    } catch (error) {
      console.error(`nav: ${(error as Error).message}`);
      return false;
    }
  };
  const ok = run();
  if (args.includes("--watch")) {
    let timer: ReturnType<typeof setTimeout> | undefined;
    watch(watched, { recursive: true }, () => {
      clearTimeout(timer);
      timer = setTimeout(run, 200);
    });
    console.log(`nav: watching ${watched}`);
  } else if (!ok) {
    process.exit(1);
  }
}
