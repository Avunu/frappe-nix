// The dev server with the staged documents and the sidebar data kept fresh: `bun run dev` stages
// docs/ and builds .generated/nav.json, then runs `jx dev` while watching docs/, so a new or renamed
// page appears in the sidebar after a reload. Arguments go to `jx dev`: `bun run dev --port 3417`.
import { join } from "node:path";
import { ROOT, docsDir, readConfig, stagedDocsDir } from "./lib/config.ts";
import { stageSite } from "./lib/stage.ts";
import { writeNav } from "./nav.ts";
import { preflight } from "./preflight.ts";
import { watch } from "node:fs";

const report = preflight(ROOT);
for (const warning of report.warnings) console.warn(`dev: warning: ${warning}`);
if (report.errors.length > 0) {
  for (const error of report.errors) console.error(`dev: ${error}`);
  process.exit(1);
}
const docs = docsDir(ROOT);
const out = join(ROOT, ".generated", "nav.json");
const config = readConfig(ROOT);
const run = () => {
  try {
    stageSite(ROOT, config);
    const { pages } = writeNav(stagedDocsDir(ROOT), out, config.name);
    console.log(`nav: ${pages} page(s)`);
  } catch (error) {
    console.error(`nav: ${(error as Error).message}`);
  }
};
run();
let timer: ReturnType<typeof setTimeout> | undefined;
watch(docs, { recursive: true }, () => {
  clearTimeout(timer);
  timer = setTimeout(run, 200);
});
const child = Bun.spawn(["bunx", "--bun", "jx", "dev", ...process.argv.slice(2)], {
  cwd: ROOT,
  stdio: ["inherit", "inherit", "inherit"],
});
// The server is a child of a child: pass every way of being asked to stop on, or it keeps the port.
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"] as const)
  process.on(signal, () => child.kill());
process.exit(await child.exited);
