import { afterAll, expect, test } from "bun:test";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { ROOT } from "./lib/config.ts";
import { cleanup, makeTree } from "./lib/test-utils.ts";
import { type Catalog, syncProjects, validateCatalog } from "./sync-projects.ts";

afterAll(cleanup);

const project = (over: Record<string, unknown> = {}) => ({
  slug: "frappe-nix",
  title: "frappe-nix",
  platform: "nixos",
  summary: "One sentence.",
  repo: "https://github.com/Avunu/frappe-nix",
  page: "https://avunu.net/open-source/frappe-nix/",
  docs: null,
  license: "MIT",
  status: "active",
  suite: null,
  ...over,
});
const catalog = (projects: unknown[] = [project()]): Catalog =>
  ({
    version: 1,
    generated: "2026-10-05T00:00:00Z",
    site: "https://avunu.net",
    projects,
  }) as Catalog;
const answer = (body: unknown, status = 200) =>
  (async () =>
    new Response(typeof body === "string" ? body : JSON.stringify(body), {
      status,
    })) as unknown as typeof fetch;

// Only the contract is asserted about the committed snapshot, never its content: CI refreshes it from
// the live catalog before the tests run, and the catalog is allowed to change (a project added,
// hidden or moved to another platform) without anything here having to change with it.
test("the committed snapshot is a valid catalog", () => {
  const snapshot = JSON.parse(readFileSync(join(ROOT, "data", "projects.snapshot.json"), "utf8"));
  expect(validateCatalog(snapshot)).toEqual([]);
  expect(snapshot.projects.length).toBeGreaterThan(0);
});

test("the contract is checked field by field", () => {
  expect(validateCatalog(catalog())).toEqual([]);
  expect(validateCatalog({ ...catalog(), version: 2 })[0]).toContain("version");
  expect(validateCatalog({ ...catalog(), projects: [] })[0]).toContain("empty");
  expect(validateCatalog(catalog([project({ platform: "erpnext" })]))[0]).toContain("platform");
  expect(validateCatalog(catalog([project({ docs: "http://x.avunu.net" })]))[0]).toContain("docs");
  expect(validateCatalog(catalog([project({ page: "javascript:alert(1)" })]))[0]).toContain("page");
  expect(validateCatalog(catalog([project(), project()]))[0]).toContain("duplicate");
  expect(
    validateCatalog(
      catalog([
        project({ docs: "https://frappe-nix.avunu.net", suite: "nix-platform", license: null }),
      ]),
    ),
  ).toEqual([]);
  expect(validateCatalog("nope")[0]).toContain("object");
  expect(validateCatalog({ version: 1, generated: "x", site: "https://avunu.net" })).toContain(
    '"projects" must be an array',
  );
});

test("a valid answer replaces the snapshot, and the same answer again changes nothing", async () => {
  const out = join(makeTree(), "data", "projects.snapshot.json");
  const first = await syncProjects({ out, fetchImpl: answer(catalog()) });
  expect(first).toMatchObject({ ok: true, changed: true });
  expect(JSON.parse(readFileSync(out, "utf8")).projects).toHaveLength(1);
  const again = await syncProjects({ out, fetchImpl: answer(catalog()) });
  expect(again).toMatchObject({ ok: true, changed: false });
});

test("every kind of failure keeps the old snapshot", async () => {
  const out = join(makeTree({ "snapshot.json": "OLD" }), "snapshot.json");
  const cases: Array<[string, typeof fetch]> = [
    ["404", answer("not found", 404)],
    ["not JSON", answer("<html>")],
    ["wrong version", answer({ ...catalog(), version: 9 })],
    ["empty", answer({ ...catalog(), projects: [] })],
    [
      "network",
      (async () => {
        throw new Error("offline");
      }) as unknown as typeof fetch,
    ],
  ];
  for (const [name, fetchImpl] of cases) {
    const result = await syncProjects({ out, fetchImpl });
    expect(result.ok, name).toBe(false);
    expect(readFileSync(out, "utf8"), name).toBe("OLD");
  }
});

test("the failure message says what was wrong", async () => {
  const out = join(makeTree(), "s.json");
  writeFileSync(out, "OLD");
  const result = await syncProjects({
    out,
    fetchImpl: answer(catalog([project({ platform: "x" })])),
  });
  expect(result.message).toContain("catalog contract");
  expect(result.message).toContain("platform");
});
