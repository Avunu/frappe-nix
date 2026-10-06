// Refreshes data/projects.snapshot.json from https://avunu.net/projects.json.
//
//   bun scripts/sync-projects.ts [--url <catalog URL>] [--out data/projects.snapshot.json] [--soft]
//
// The snapshot is what the project switcher shows before (and without) the live fetch, so it must be
// a real catalog: this script checks the document against the version 1 contract before it replaces
// the file and keeps the old snapshot when the answer is wrong. With --soft a failure is a warning
// and the exit code stays 0 (the workflow uses it: a build must not depend on avunu.net being up).
//
// The contract (version 1): { version, generated, site, projects: [{ slug, title, platform, summary,
// repo, page, docs, license, status, suite }] }; platform is one of frappe, odoo, wordpress, nixos,
// general; docs is null until a project has its own docs site.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { parseArgs } from "node:util";
import { PLATFORMS, ROOT } from "./lib/config.ts";

export const CATALOG_URL = "https://avunu.net/projects.json";

export interface CatalogProject {
  slug: string;
  title: string;
  platform: string;
  summary: string;
  repo: string;
  page: string;
  docs: string | null;
  license: string | null;
  status: string;
  suite: string | null;
}

export interface Catalog {
  version: 1;
  generated: string;
  site: string;
  projects: CatalogProject[];
}

const isHttps = (value: unknown): value is string =>
  typeof value === "string" && /^https:\/\/[^\s]+$/.test(value);

/** Every way a document differs from the version 1 contract, as sentences. Empty means it conforms. */
export function validateCatalog(doc: unknown): string[] {
  const problems: string[] = [];
  if (doc === null || typeof doc !== "object" || Array.isArray(doc))
    return ["the document is not a JSON object"];
  const catalog = doc as Record<string, unknown>;
  if (catalog.version !== 1)
    problems.push(`"version" must be 1 (got ${JSON.stringify(catalog.version)})`);
  if (typeof catalog.generated !== "string")
    problems.push('"generated" must be a timestamp string');
  if (!isHttps(catalog.site)) problems.push('"site" must be an https URL');
  if (!Array.isArray(catalog.projects)) return [...problems, '"projects" must be an array'];
  if (catalog.projects.length === 0) problems.push('"projects" is empty');
  const seen = new Set<string>();
  catalog.projects.forEach((entry, index) => {
    const at = `projects[${index}]`;
    if (entry === null || typeof entry !== "object")
      return void problems.push(`${at} is not an object`);
    const p = entry as Record<string, unknown>;
    const name = typeof p.slug === "string" ? p.slug : at;
    for (const key of ["slug", "title", "summary"] as const) {
      if (typeof p[key] !== "string" || p[key] === "")
        problems.push(`${name}: "${key}" must be a non-empty string`);
    }
    if (typeof p.slug === "string") {
      if (seen.has(p.slug)) problems.push(`${name}: duplicate slug`);
      seen.add(p.slug);
    }
    if (!(PLATFORMS as readonly string[]).includes(String(p.platform)))
      problems.push(`${name}: "platform" must be one of ${PLATFORMS.join(", ")}`);
    if (!isHttps(p.repo)) problems.push(`${name}: "repo" must be an https URL`);
    if (!isHttps(p.page)) problems.push(`${name}: "page" must be an https URL`);
    if (p.docs !== null && !isHttps(p.docs))
      problems.push(`${name}: "docs" must be an https URL or null`);
    if (p.license !== null && typeof p.license !== "string")
      problems.push(`${name}: "license" must be a string or null`);
    if (typeof p.status !== "string") problems.push(`${name}: "status" must be a string`);
    if (p.suite !== null && typeof p.suite !== "string")
      problems.push(`${name}: "suite" must be a string or null`);
  });
  return problems;
}

export interface SyncResult {
  ok: boolean;
  changed: boolean;
  message: string;
}

/** Fetches the catalog and replaces `out` when the answer is a valid catalog. Never throws. */
export async function syncProjects(options: {
  url?: string;
  out: string;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
}): Promise<SyncResult> {
  const url = options.url ?? CATALOG_URL;
  const doFetch = options.fetchImpl ?? fetch;
  let text: string;
  try {
    const response = await doFetch(url, {
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(options.timeoutMs ?? 10_000),
    });
    if (!response.ok)
      return {
        ok: false,
        changed: false,
        message: `${url} answered ${response.status}; the snapshot is unchanged`,
      };
    text = await response.text();
  } catch (error) {
    return {
      ok: false,
      changed: false,
      message: `could not fetch ${url} (${(error as Error).message}); the snapshot is unchanged`,
    };
  }
  let doc: unknown;
  try {
    doc = JSON.parse(text);
  } catch {
    return {
      ok: false,
      changed: false,
      message: `${url} did not return JSON; the snapshot is unchanged`,
    };
  }
  const problems = validateCatalog(doc);
  if (problems.length > 0) {
    return {
      ok: false,
      changed: false,
      message: `${url} does not match the catalog contract; the snapshot is unchanged:\n  - ${problems.slice(0, 8).join("\n  - ")}`,
    };
  }
  const next = `${JSON.stringify(doc, null, 2)}\n`;
  const previous = existsSync(options.out) ? readFileSync(options.out, "utf8") : "";
  if (next === previous)
    return {
      ok: true,
      changed: false,
      message: `already current (${(doc as Catalog).projects.length} projects)`,
    };
  mkdirSync(dirname(options.out), { recursive: true });
  writeFileSync(options.out, next);
  return {
    ok: true,
    changed: true,
    message: `wrote ${(doc as Catalog).projects.length} projects (generated ${(doc as Catalog).generated})`,
  };
}

if (import.meta.main) {
  const { values } = parseArgs({
    args: process.argv.slice(2),
    options: { url: { type: "string" }, out: { type: "string" }, soft: { type: "boolean" } },
  });
  const out = resolve(ROOT, values.out ?? join("data", "projects.snapshot.json"));
  const result = await syncProjects({ url: values.url, out });
  const line = `sync-projects: ${result.message}`;
  if (result.ok) console.log(line);
  else if (values.soft)
    console.warn(`${line}\nsync-projects: continuing with the committed snapshot`);
  else {
    console.error(line);
    process.exit(1);
  }
}
