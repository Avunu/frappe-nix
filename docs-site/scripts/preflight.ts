// Checks, before anything is built, that the site is set up and that the installed Jx has the
// vault-content features the documentation depends on. `bun run build` and `bun run dev` run it.
//
//   bun scripts/preflight.ts
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { readSnapshot } from "./lib/catalog.ts";
import { ROOT, docsDir, isPlaceholder, readConfig, validateConfig } from "./lib/config.ts";

export interface Report {
  errors: string[];
  warnings: string[];
}

const flat = (slug: string) => slug.replaceAll("_", "-");

/**
 * What the committed catalog snapshot says about the configured slug: an error when a different
 * spelling of it is the catalog's key (`erpnext-taskview` for `erpnext_taskview`: the switcher would
 * never mark the project), a warning when it is not in the catalog at all, nothing when it is there.
 */
export function checkSlug(root: string, slug: string): { error?: string; warning?: string } {
  const slugs = readSnapshot(root).map((p) => p.slug);
  if (slugs.length === 0 || slugs.includes(slug)) return {};
  const close = slugs.find((candidate) => flat(candidate) === flat(slug));
  if (close) {
    return {
      error: `docs.config.json: slug "${slug}" is not in the project catalog, but "${close}" is: the switcher would never mark this project as the current one. Set "slug" to "${close}" (the domain stays as it is).`,
    };
  }
  return {
    warning: `slug "${slug}" is not in data/projects.snapshot.json, so the project switcher will not mark this project as the current one. Add the project to the avunu.net catalog first, then run bun run sync:projects.`,
  };
}

/** The parser files that only exist in a Jx release with the vault-content features. */
export const REQUIRED_PARSER_FILES = [
  "src/content-routes.ts",
  "src/content-links.ts",
  "src/alerts.ts",
];

export function checkJx(root: string = ROOT): string | null {
  const parser = join(root, "node_modules", "@jxsuite", "parser");
  if (!existsSync(parser)) return "@jxsuite/parser is not installed. Run bun install.";
  const missing = REQUIRED_PARSER_FILES.filter((file) => !existsSync(join(parser, file)));
  if (missing.length === 0) return null;
  const version = (() => {
    try {
      return (
        (JSON.parse(readFileSync(join(parser, "package.json"), "utf8")) as { version?: string })
          .version ?? "unknown"
      );
    } catch {
      return "unknown";
    }
  })();
  return (
    `The installed @jxsuite/parser (${version}) predates the vault-content features this site needs ` +
    "(GitHub alerts, relative links, route templates; jxsuite/jx pull request 426). " +
    "Install the release that includes them (parser 2.0.0 or newer), or link a checkout: bun run link:jx /path/to/jx. See the README."
  );
}

export function preflight(root: string = ROOT, options: { jx?: boolean } = {}): Report {
  const report: Report = { errors: [], warnings: [] };
  let config;
  try {
    config = readConfig(root);
  } catch (error) {
    report.errors.push((error as Error).message);
    return report;
  }
  if (isPlaceholder(config)) {
    report.errors.push(
      'docs.config.json still has the template values. Run: bun scripts/init.ts --name "<Project>" ...',
    );
  }
  for (const problem of validateConfig(config)) report.errors.push(`docs.config.json: ${problem}`);

  const project = JSON.parse(readFileSync(join(root, "project.json"), "utf8")) as {
    name?: string;
    url?: string;
  };
  if (project.name !== config.name || project.url !== `https://${config.domain}`) {
    report.errors.push(
      `project.json (name "${project.name}", url "${project.url}") does not match docs.config.json (name "${config.name}", domain "${config.domain}"). Run init again, or edit one to match the other.`,
    );
  }
  const cname = join(root, "public", "CNAME");
  if (existsSync(cname) && readFileSync(cname, "utf8").trim() !== config.domain) {
    report.errors.push(`public/CNAME does not say ${config.domain}`);
  }
  const docs = docsDir(root);
  if (!existsSync(docs)) {
    report.errors.push(
      "The documentation folder ../docs does not exist. Put your Markdown in docs/ at the repository root; docs/README.md is the home page.",
    );
  } else if (!readdirSync(docs).some((name) => /^(?:readme|index)\.md$/i.test(name))) {
    report.errors.push(
      "docs/README.md is missing: it is the documentation home (/docs/). Add one (docs/index.md works too); for a repository whose README is its documentation: cp README.md docs/README.md",
    );
  }
  const slug = config.slug ? checkSlug(root, config.slug) : {};
  if (slug.error) report.errors.push(slug.error);
  if (slug.warning) report.warnings.push(slug.warning);
  if (options.jx !== false) {
    const jx = checkJx(root);
    if (jx) report.errors.push(jx);
  }
  return report;
}

if (import.meta.main) {
  const report = preflight();
  for (const warning of report.warnings) console.warn(`preflight: warning: ${warning}`);
  if (report.errors.length > 0) {
    for (const error of report.errors) console.error(`preflight: ${error}`);
    process.exit(1);
  }
  console.log("preflight: OK");
}
