// docs.config.json: the few facts about a project that the site needs. init.ts writes it,
// preflight (scripts/preflight.ts) and the tests check it, and the layouts read it at build time.
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

export const PLATFORMS = ["frappe", "odoo", "wordpress", "nixos", "general"] as const;
export type Platform = (typeof PLATFORMS)[number];

export interface DocsConfig {
  /** Display name, shown in the header, the title and the footer. */
  name: string;
  /** One sentence: what the project does and who it is for. */
  tagline: string;
  /** The project's slug in https://avunu.net/projects.json (it may have underscores): marks the current project in the switcher. */
  slug: string;
  platform: Platform;
  /** The GitHub repository URL. */
  repo: string;
  /** The custom domain, without a scheme: the slug with hyphens, then .avunu.net. */
  domain: string;
  /** SPDX license identifier. */
  license: string;
  /**
   * Optional: the repository's default branch (default main). "Edit this page" and the GitHub links
   * the build writes point at it, and the workflow publishes from it (init writes it there too).
   */
  branch?: string;
}

export const ROOT = resolve(import.meta.dir, "..", "..");

/** The folder of Markdown: docs/ at the repository root, beside this site folder. It is always docs/. */
export const docsDir = (root: string = ROOT): string => join(root, "..", "docs");

/**
 * The copy of docs/ that Jx reads (`content.docs.source` in project.json). The build makes it from
 * docs/ (scripts/lib/stage.ts): links to files of the repository that are not pages become GitHub
 * links, and a comment that precedes the frontmatter is moved behind it.
 */
export const stagedDocsDir = (root: string = ROOT): string => join(root, ".generated", "docs");

/** The text init writes for a tagline nobody has filled in; preflight refuses it. */
export const PLACEHOLDER_TAGLINE =
  "One sentence that says what this project does and who it is for.";

export function readConfig(root: string = ROOT): DocsConfig {
  const file = join(root, "docs.config.json");
  if (!existsSync(file)) throw new Error(`docs.config.json is missing in ${root}`);
  return JSON.parse(readFileSync(file, "utf8")) as DocsConfig;
}

// A slug is a catalog key, so it may have underscores (erpnext_taskview); a domain label may not.
const SLUG = /^[a-z0-9][a-z0-9_-]*$/;
const DOMAIN = /^(?=.{4,253}$)([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$/;
// A branch name that is safe to write into a workflow file and a URL: no spaces, quotes, "..", or a leading dash.
export const BRANCH = /^(?!.*\.\.)(?!.*\/\/)[A-Za-z0-9_][\w./-]{0,99}$/;

/** Everything wrong with a configuration, as sentences. Empty means valid. */
export function validateConfig(config: Partial<DocsConfig>): string[] {
  const problems: string[] = [];
  const need = (key: keyof DocsConfig) => {
    const value = config[key];
    if (typeof value !== "string" || value.trim() === "") problems.push(`"${key}" is required`);
  };
  for (const key of ["name", "tagline", "slug", "platform", "repo", "domain", "license"] as const)
    need(key);
  if (config.slug && !SLUG.test(config.slug)) {
    problems.push(
      `"slug" must be lowercase letters, digits, hyphens and underscores (got "${config.slug}")`,
    );
  }
  if (config.platform && !(PLATFORMS as readonly string[]).includes(config.platform)) {
    problems.push(`"platform" must be one of ${PLATFORMS.join(", ")} (got "${config.platform}")`);
  }
  if (config.repo && !/^https:\/\/github\.com\/[\w.-]+\/[\w.-]+$/.test(config.repo)) {
    problems.push(`"repo" must look like https://github.com/Avunu/project (got "${config.repo}")`);
  }
  if (config.domain && !DOMAIN.test(config.domain)) {
    problems.push(
      `"domain" must be a host name without a scheme, like project.avunu.net (got "${config.domain}")`,
    );
  }
  if (config.tagline && config.tagline.length > 200)
    problems.push(`"tagline" should be one sentence (200 characters at most)`);
  if (config.branch !== undefined && !BRANCH.test(config.branch)) {
    problems.push(
      `"branch" must be a plain branch name such as main, develop or 18.0 (got "${config.branch}")`,
    );
  }
  return problems;
}

/** The placeholder configuration the template ships with. */
export function isPlaceholder(config: Partial<DocsConfig>): boolean {
  return (
    config.slug === "project-name" ||
    config.name === "Project Name" ||
    config.tagline === PLACEHOLDER_TAGLINE
  );
}
