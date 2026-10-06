// Everything CI runs, in order, stopping at the first failure:
//
//   bun run check [--lenient]
//
//   preflight   the site is set up and the installed Jx packages are the releases it needs
//   tests       bun test scripts (the helpers, the sidebar, init, the post-build fixes, the layouts)
//   contrast    WCAG AA for every colour pair, light and dark
//   build       nav data, jx build, post-build fixes; document problems fail the build
//   links       every link, anchor, search result and sidebar entry in dist/ resolves
import { ROOT } from "./lib/config.ts";

const lenient = process.argv.includes("--lenient");
const steps: Array<[name: string, command: string[]]> = [
  ["preflight", ["bun", "scripts/preflight.ts"]],
  ["tests", ["bun", "test", "scripts"]],
  ["contrast", ["bun", "scripts/check-contrast.ts"]],
  ["build", ["bun", "scripts/build.ts", lenient ? "--lenient" : "--strict"]],
  ["links", ["bun", "scripts/check-links.ts"]],
];

for (const [name, command] of steps) {
  console.log(`\n== ${name} ==`);
  const result = Bun.spawnSync(command, { cwd: ROOT, stdout: "inherit", stderr: "inherit" });
  if (result.exitCode !== 0) {
    console.error(`\ncheck: ${name} failed`);
    process.exit(result.exitCode ?? 1);
  }
}
console.log("\ncheck: all steps passed");
