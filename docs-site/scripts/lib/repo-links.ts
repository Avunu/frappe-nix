// Links from a documentation page to a file or folder of the repository that is not a page: source
// files, LICENSE, a folder of examples. A README copied into docs/ is full of them, written
// relative to the repository root. They cannot be pages, so the build turns each one that exists in
// the repository into a link to it on GitHub (`/blob/` for a file, `/tree/` for a folder, and the raw
// file for an image), and leaves the rest for scripts/check-links.ts to report.
import { existsSync, statSync } from "node:fs";
import { join, posix, relative, resolve, sep } from "node:path";
import { fileFor } from "./links.ts";

export interface RepoLinkOptions {
  /** The site's dist/ folder: a link that already resolves there is left alone. */
  dist: string;
  /** The repository root. */
  repoRoot: string;
  /** The folder the Markdown lives in (the repository's docs/). */
  docsDir: string;
  /** https://github.com/<owner>/<name> */
  repoUrl: string;
  branch: string;
}

export interface RepoLink {
  page: string;
  from: string;
  to: string;
}

const entities = (value: string) =>
  value.replaceAll("&amp;", "&").replaceAll("&quot;", '"').replaceAll("&#39;", "'");
const encodeAttr = (value: string) => value.replaceAll("&", "&amp;").replaceAll('"', "&quot;");

function inside(root: string, path: string): boolean {
  const rel = relative(root, path);
  return rel === "" || (!rel.startsWith("..") && !rel.includes(`..${sep}`) && !rel.startsWith(sep));
}

/** The repository path (relative to the repo root, `/`-separated) a link points at, or null. */
export function repoTarget(
  href: string,
  sourceDir: string,
  options: Pick<RepoLinkOptions, "repoRoot" | "docsDir">,
): string | null {
  const bare = href.split("#")[0]!.split("?")[0]!;
  if (
    bare === "" ||
    bare.startsWith("/") ||
    /^[a-z][a-z0-9+.-]*:/i.test(bare) ||
    bare.startsWith("//")
  )
    return null;
  let decoded = bare;
  try {
    decoded = decodeURIComponent(bare);
  } catch {
    // keep the raw text
  }
  for (const base of [join(options.docsDir, sourceDir), options.repoRoot]) {
    const candidate = resolve(base, decoded);
    if (inside(options.repoRoot, candidate) && existsSync(candidate)) {
      return relative(options.repoRoot, candidate).split(sep).join("/");
    }
  }
  return null;
}

export function githubUrl(
  options: Pick<RepoLinkOptions, "repoUrl" | "branch" | "repoRoot">,
  repoPath: string,
  asset: boolean,
): string {
  const full = join(options.repoRoot, repoPath);
  const isDir = statSync(full).isDirectory();
  const kind = asset && !isDir ? "raw" : isDir ? "tree" : "blob";
  const path =
    repoPath === ""
      ? ""
      : `/${posix.normalize(repoPath).split("/").map(encodeURIComponent).join("/")}`;
  return `${options.repoUrl}/${kind}/${options.branch}${path}`;
}

/**
 * Rewrites the broken repository-relative links of one page. `route` is the page's address and
 * `sourceDir` the folder of its Markdown file inside docs/ ("" at the top). Returns the new HTML and
 * the links it changed.
 */
export function rewriteRepoLinks(
  html: string,
  route: string,
  sourceDir: string,
  options: RepoLinkOptions,
): { html: string; links: RepoLink[] } {
  const links: RepoLink[] = [];
  const out = html.replace(/<(a|img)\b([^>]*)>/gi, (tag, name: string, attrs: string) => {
    const attribute = name.toLowerCase() === "a" ? "href" : "src";
    const match = new RegExp(`\\s${attribute}=("([^"]*)"|'([^']*)')`).exec(attrs);
    if (!match) return tag;
    const value = entities(match[2] ?? match[3] ?? "");
    if (
      value === "" ||
      value.startsWith("#") ||
      /^[a-z][a-z0-9+.-]*:/i.test(value) ||
      value.startsWith("//") ||
      value.startsWith("/")
    )
      return tag;
    // Resolves in the built site (a page, a copied image, an anchor): leave it alone.
    const bare = value.split("#")[0]!.split("?")[0]!;
    const served = posix.join(route.endsWith("/") ? route : `${posix.dirname(route)}/`, bare);
    if (
      bare !== "" &&
      fileFor(options.dist, served + (bare.endsWith("/") && !served.endsWith("/") ? "/" : ""))
    )
      return tag;
    const target = repoTarget(value, sourceDir, options);
    if (target === null) return tag;
    const hash = value.includes("#") ? `#${value.split("#").slice(1).join("#")}` : "";
    const url = githubUrl(options, target, attribute === "src") + hash;
    links.push({ page: route, from: value, to: url });
    return tag.replace(match[0], ` ${attribute}="${encodeAttr(url)}"`);
  });
  return { html: out, links };
}
