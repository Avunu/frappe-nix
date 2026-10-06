// Helpers for the tests: throwaway folders that are removed afterwards, and where the workflow is.
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join } from "node:path";
import { ROOT } from "./config.ts";

/**
 * The deploy workflow and where it is. In the template it is inside the site folder
 * (`.github/workflows/docs.yml`, with the folder called docs-site); `init` moves it to the
 * repository's .github folder, so in an adopting repository it is one level up and the folder is
 * called whatever the repository chose. Tests of the workflow run in both, and skip when there is none.
 */
export function workflowLocation(): { file: string; folder: string; template: boolean } | null {
  const inside = join(ROOT, ".github", "workflows", "docs.yml");
  if (existsSync(inside)) return { file: inside, folder: "docs-site", template: true };
  const above = join(ROOT, "..", ".github", "workflows", "docs.yml");
  if (existsSync(above)) return { file: above, folder: basename(ROOT), template: false };
  return null;
}

const made: string[] = [];

/** Creates a temporary folder holding `files` (path -> text) and returns its path. */
export function makeTree(files: Record<string, string> = {}): string {
  const root = mkdtempSync(join(tmpdir(), "docs-site-test-"));
  made.push(root);
  for (const [path, text] of Object.entries(files)) {
    const file = join(root, path);
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, text);
  }
  return root;
}

/** Removes every folder makeTree created. Call it from afterAll. */
export function cleanup(): void {
  for (const dir of made.splice(0)) rmSync(dir, { recursive: true, force: true });
}
