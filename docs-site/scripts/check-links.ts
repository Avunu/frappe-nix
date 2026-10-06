// Crawls dist/ and fails on any broken internal link, asset, heading anchor or search result, on
// pages without exactly one <h1>, and on sidebar entries that were not built.
//
//   bun run check:links [distDir]
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { ROOT } from "./lib/config.ts";
import { checkLinks, formatIssues } from "./lib/links.ts";
import type { NavData } from "./lib/nav.ts";

if (import.meta.main) {
  const dist = resolve(
    process.argv[2] && !process.argv[2].startsWith("--") ? process.argv[2] : join(ROOT, "dist"),
  );
  if (!existsSync(dist)) {
    console.error(`check-links: ${dist} does not exist. Run bun run build first.`);
    process.exit(1);
  }
  const navFile = join(ROOT, ".generated", "nav.json");
  const nav = existsSync(navFile)
    ? (JSON.parse(readFileSync(navFile, "utf8")) as NavData)
    : undefined;
  const report = checkLinks(dist, { nav });
  console.log(`check-links: ${report.pages} page(s), ${report.checked} reference(s) checked`);
  if (report.warnings.length > 0)
    console.log(`\n${report.warnings.length} warning(s):\n${formatIssues(report.warnings)}`);
  if (report.errors.length > 0) {
    console.error(`\n${report.errors.length} ERROR(S):\n${formatIssues(report.errors)}`);
    process.exit(1);
  }
  console.log("check-links: OK");
}
