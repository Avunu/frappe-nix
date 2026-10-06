// The committed snapshot of https://avunu.net/projects.json (data/projects.snapshot.json), read by
// init (to fill in what the catalog already knows about a repository) and by preflight (to check
// the slug). The build-time and in-browser switcher read it themselves.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { CatalogProject } from "../sync-projects.ts";

/** The entries of the snapshot in `siteDir`, or [] when there is none or it cannot be read. */
export function readSnapshot(siteDir: string): CatalogProject[] {
  const file = join(siteDir, "data", "projects.snapshot.json");
  if (!existsSync(file)) return [];
  try {
    const catalog = JSON.parse(readFileSync(file, "utf8")) as { projects?: unknown };
    return Array.isArray(catalog.projects)
      ? (catalog.projects as CatalogProject[]).filter((p) => p && typeof p.slug === "string")
      : [];
  } catch {
    return [];
  }
}

/** `https://github.com/Avunu/X/` and `https://github.com/avunu/x.git` name the same repository. */
export const sameRepo = (a: string, b: string): boolean =>
  a
    .replace(/\.git$/, "")
    .replace(/\/+$/, "")
    .toLowerCase() ===
  b
    .replace(/\.git$/, "")
    .replace(/\/+$/, "")
    .toLowerCase();

/** The last part of a GitHub URL: `erpnext_taskview` for https://github.com/Avunu/erpnext_taskview. */
export const repoName = (repo: string): string =>
  repo
    .replace(/\.git$/, "")
    .replace(/\/+$/, "")
    .split("/")
    .pop() ?? "";

/**
 * The catalog entry for a repository. A repository can have several entries (avunu-odoo-addons has
 * five): the one named like the repository is the site's project; with none of that name the answer
 * is ambiguous and the entries are listed so the caller can ask for `--slug`.
 */
export function entryFor(
  projects: CatalogProject[],
  repo: string,
): { entry?: CatalogProject; ambiguous: CatalogProject[] } {
  const entries = projects.filter((p) => sameRepo(p.repo, repo));
  if (entries.length === 1) return { entry: entries[0], ambiguous: [] };
  const named = entries.find((p) => p.slug.toLowerCase() === repoName(repo).toLowerCase());
  if (named) return { entry: named, ambiguous: [] };
  return { ambiguous: entries };
}
