// The production build: preflight, a staged copy of docs/, the Markdown checks, the sidebar data,
// `jx build`, then the post-build fixes.
//
//   bun scripts/build.ts [--strict] [--lenient]
//
// Jx prints a warning for every document problem it works around: a link to a file that is not a
// published page (shown as plain text), two files with one address, a field of the wrong type, an
// image that is not there. A build with such a warning would publish something the author did not
// intend, so in CI (CI=true or --strict) any of them fails the build, as do the Markdown problems
// that lose text (scripts/lib/lint.ts). --lenient (or DOCS_LENIENT=1) keeps them as warnings, for the
// first pass over a repository whose README was written for GitHub.
import { join } from "node:path";
import { ROOT, docsDir, readConfig, stagedDocsDir } from "./lib/config.ts";
import { formatIssue, lintDocs } from "./lib/lint.ts";
import { stageSite } from "./lib/stage.ts";
import { runPostbuild } from "./postbuild.ts";
import { writeNav } from "./nav.ts";
import { preflight } from "./preflight.ts";

/**
 * Lines of `jx build` output that mean the documents have a problem (not a hint about hosting):
 * every `Content …` warning (links, routes, ids, validation, callouts, relationships, assets), every
 * `Warning: …` (an unserved route, a missing asset) and every error.
 */
export const PROBLEM = /^(?:Content\b|Warning:|Error)/;

export function problemsIn(output: string): string[] {
  return output
    .split("\n")
    .filter((line) => PROBLEM.test(line.trim()))
    .map((line) => line.trim());
}

/** Runs `fn`; an Error becomes one `build: message` line and exit code 1, never a stack trace. */
export function orExit<T>(fn: () => T): T {
  try {
    return fn();
  } catch (error) {
    console.error(`build: ${(error as Error).message}`);
    process.exit(1);
  }
}

if (import.meta.main) {
  const args = process.argv.slice(2);
  const lenient = args.includes("--lenient") || process.env.DOCS_LENIENT === "1";
  const strict = !lenient && (args.includes("--strict") || process.env.CI === "true");

  const report = preflight(ROOT);
  for (const warning of report.warnings) console.warn(`preflight: warning: ${warning}`);
  if (report.errors.length > 0) {
    for (const error of report.errors) console.error(`build: ${error}`);
    process.exit(1);
  }
  const config = readConfig(ROOT);

  const staged = orExit(() => stageSite(ROOT, config));
  for (const link of staged.links)
    console.log(`stage: docs/${link.file}:${link.line}  ${link.from} -> ${link.to}`);
  for (const file of staged.comments)
    console.log(`stage: docs/${file}  moved the comment above the frontmatter below it`);
  console.log(
    `stage: ${staged.files} file(s), ${staged.links.length} link(s) to repository files rewritten`,
  );

  const lint = lintDocs(docsDir(ROOT));
  for (const issue of lint)
    console[issue.level === "error" ? "error" : "warn"](
      `lint: ${issue.level}: ${formatIssue(issue)}`,
    );
  const lintErrors = lint.filter((issue) => issue.level === "error");

  const { pages, warnings, nav } = orExit(() =>
    writeNav(stagedDocsDir(ROOT), join(ROOT, ".generated", "nav.json"), config.name),
  );
  for (const warning of warnings) console.warn(`nav: warning: ${warning}`);
  console.log(`nav: ${pages} page(s)`);

  const jx = Bun.spawnSync(["bunx", "--bun", "jx", "build"], {
    cwd: ROOT,
    stdout: "pipe",
    stderr: "pipe",
  });
  const output = `${jx.stdout.toString()}${jx.stderr.toString()}`;
  process.stdout.write(output);
  if (jx.exitCode !== 0) {
    console.error("build: jx build failed");
    process.exit(jx.exitCode ?? 1);
  }
  const problems = [...problemsIn(output), ...lintErrors.map(formatIssue)];
  if (problems.length > 0 && strict) {
    console.error(
      `\nbuild: ${problems.length} document problem(s) above fail the build. Fix the documents, or run with --lenient while you work through them.`,
    );
    process.exit(1);
  }

  const summary = runPostbuild(join(ROOT, "dist"), config, nav, {
    repoRoot: join(ROOT, ".."),
    docsDir: docsDir(ROOT),
  });
  for (const link of summary.repoLinks)
    console.log(`repo link: ${link.page}  ${link.from} -> ${link.to}`);
  for (const warning of summary.warnings) console.warn(`postbuild: warning: ${warning}`);
  console.log(
    `postbuild: ${summary.pages} page(s), ${summary.sitemapUrls} in the sitemap, ${summary.canonicals} canonical URL(s) set, ${summary.searchTitles} search title(s) fixed, CNAME ${summary.cname ? "present" : "absent"}`,
  );
}
