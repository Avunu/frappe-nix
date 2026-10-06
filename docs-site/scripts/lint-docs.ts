// The Markdown checks on their own: what the site cannot render, with file and line.
//
//   bun run lint:docs
//
// `bun run build` runs the same checks; errors fail a strict build, warnings are printed.
import { ROOT, docsDir } from "./lib/config.ts";
import { formatIssue, lintDocs } from "./lib/lint.ts";

const issues = lintDocs(docsDir(ROOT));
for (const issue of issues)
  console[issue.level === "error" ? "error" : "warn"](`${issue.level}: ${formatIssue(issue)}`);
const errors = issues.filter((issue) => issue.level === "error").length;
console.log(`lint-docs: ${errors} error(s), ${issues.length - errors} warning(s)`);
process.exit(errors === 0 ? 0 : 1);
