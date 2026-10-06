import { afterAll, expect, test } from "bun:test";
import { existsSync, lstatSync, readlinkSync } from "node:fs";
import { join } from "node:path";
import { linkJx } from "./link-jx.ts";
import { cleanup, makeTree } from "./lib/test-utils.ts";

afterAll(cleanup);

const checkout = (skip: string[] = []) => {
  const files: Record<string, string> = {
    "packages/compiler/dist/cli.js": "",
    "packages/compiler/bin/jx.js": "#!/usr/bin/env node\n",
    "extensions/parser/src/content-routes.ts": "",
    "extensions/search/package.json": "{}",
    "packages/server/package.json": "{}",
  };
  for (const file of skip) delete files[file];
  return makeTree(files);
};

test("links the packages and the jx binary into node_modules", () => {
  const jx = checkout();
  const site = makeTree({ "node_modules/@jxsuite/parser/package.json": "{}" });
  const linked = linkJx(jx, site);
  expect(linked.map((l) => l.split(" ")[0])).toEqual([
    "@jxsuite/compiler",
    "@jxsuite/parser",
    "@jxsuite/search",
    "@jxsuite/server",
    "node_modules/.bin/jx",
  ]);
  for (const [name, path] of [
    ["compiler", "packages/compiler"],
    ["parser", "extensions/parser"],
    ["search", "extensions/search"],
  ] as const) {
    const link = join(site, "node_modules", "@jxsuite", name);
    expect(lstatSync(link).isSymbolicLink()).toBe(true);
    expect(readlinkSync(link)).toBe(join(jx, path));
  }
  expect(existsSync(join(site, "node_modules", ".bin", "jx"))).toBe(true);
  // Running it again replaces its own links.
  expect(() => linkJx(jx, site)).not.toThrow();
});

test("a checkout that has not been built is refused with the command to run", () => {
  expect(() => linkJx(checkout(["packages/compiler/dist/cli.js"]), makeTree())).toThrow(
    /bun install && bun run build/,
  );
});

test("a checkout without the vault-content parser is refused", () => {
  expect(() => linkJx(checkout(["extensions/parser/src/content-routes.ts"]), makeTree())).toThrow(
    /vault-content/,
  );
});

test("a package the checkout does not have is skipped, not an error", () => {
  const linked = linkJx(checkout(["packages/server/package.json"]), makeTree());
  expect(linked.some((l) => l.includes("server"))).toBe(false);
  expect(linked.some((l) => l.includes("compiler"))).toBe(true);
});
