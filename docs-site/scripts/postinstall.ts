// Runs after `bun install`: generates the editor schemas (project.schema.json, document.schema.json)
// that project.json and the layouts point at. Failing to generate them never fails the install, and
// installing a Jx release that predates the vault-content features is reported by `bun run build`.
import { existsSync } from "node:fs";
import { join } from "node:path";
import { ROOT } from "./lib/config.ts";

const bin = join(ROOT, "node_modules", ".bin", "jx");
if (existsSync(bin)) {
  const result = Bun.spawnSync(["bun", "--bun", bin, "schema"], {
    cwd: ROOT,
    stdout: "pipe",
    stderr: "pipe",
  });
  if (result.exitCode !== 0) {
    console.warn(
      "postinstall: `jx schema` did not run; editor validation is unavailable (the build is unaffected).",
    );
  }
}
