// Uses a local checkout of Jx instead of the packages from npm. Needed only while the Jx release
// with the vault-content features (jxsuite/jx pull request 426) is unpublished: the docs read
// GitHub alerts, relative links, `exclude`/`where` filters and `route` templates from it.
//
//   git clone https://github.com/jxsuite/jx && cd jx
//   git checkout feat/parser-vault-content && bun install && bun run build
//   cd <this site> && bun install && bun run link:jx /path/to/jx
//
// It replaces node_modules/@jxsuite/{compiler,parser,search,server} with links into the checkout and
// links node_modules/.bin/jx. Run it again after every `bun install`, which restores the registry
// copies. Once Jx publishes the release, delete this step: `bun update @jxsuite/parser` is enough.
import { existsSync, lstatSync, mkdirSync, rmSync, symlinkSync } from "node:fs";
import { join, resolve } from "node:path";
import { ROOT } from "./lib/config.ts";

export const LINKS: Array<[name: string, path: string]> = [
  ["compiler", "packages/compiler"],
  ["parser", "extensions/parser"],
  ["search", "extensions/search"],
  ["server", "packages/server"],
];

export function linkJx(checkout: string, siteDir: string = ROOT): string[] {
  const jx = resolve(checkout);
  if (!existsSync(join(jx, "packages", "compiler", "dist", "cli.js"))) {
    throw new Error(
      `${jx} has no built compiler (packages/compiler/dist/cli.js). In that checkout run: bun install && bun run build`,
    );
  }
  if (!existsSync(join(jx, "extensions", "parser", "src", "content-routes.ts"))) {
    throw new Error(
      `${jx} does not have the vault-content parser (extensions/parser/src/content-routes.ts). Check out feat/parser-vault-content or a release that includes it.`,
    );
  }
  const scope = join(siteDir, "node_modules", "@jxsuite");
  const bin = join(siteDir, "node_modules", ".bin");
  mkdirSync(scope, { recursive: true });
  mkdirSync(bin, { recursive: true });
  const linked: string[] = [];
  for (const [name, path] of LINKS) {
    const target = join(jx, path);
    if (!existsSync(target)) continue;
    const link = join(scope, name);
    if (existsSync(link) || isLink(link)) rmSync(link, { recursive: true, force: true });
    symlinkSync(target, link, "dir");
    linked.push(`@jxsuite/${name} -> ${target}`);
  }
  const jxBin = join(bin, "jx");
  if (existsSync(jxBin) || isLink(jxBin)) rmSync(jxBin, { force: true });
  symlinkSync("../@jxsuite/compiler/bin/jx.js", jxBin);
  linked.push("node_modules/.bin/jx");
  return linked;
}

function isLink(path: string): boolean {
  try {
    return lstatSync(path).isSymbolicLink();
  } catch {
    return false;
  }
}

if (import.meta.main) {
  const checkout = process.argv[2] ?? process.env.JX_CHECKOUT;
  if (!checkout) {
    console.error("usage: bun run link:jx /path/to/a/built/jx/checkout   (or set JX_CHECKOUT)");
    process.exit(1);
  }
  try {
    for (const line of linkJx(checkout)) console.log(`link-jx: ${line}`);
    const schema = Bun.spawnSync(
      ["bun", "--bun", join(ROOT, "node_modules", ".bin", "jx"), "schema"],
      { cwd: ROOT, stdout: "pipe", stderr: "pipe" },
    );
    console.log(
      schema.exitCode === 0
        ? "link-jx: regenerated the editor schemas"
        : "link-jx: `jx schema` failed (editor validation only)",
    );
  } catch (error) {
    console.error(`link-jx: ${(error as Error).message}`);
    process.exit(1);
  }
}
