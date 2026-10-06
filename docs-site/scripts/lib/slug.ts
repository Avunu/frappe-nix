// URL slugs for documentation routes. This is the algorithm the Jx parser applies to a content
// type's `route` template (`{id:slug}`, `{dir:slug}`; extensions/parser/src/content-routes.ts in
// jxsuite/jx, MIT), kept here so scripts/nav.ts can write the sidebar's links before Jx runs. The
// build verifies the two agree: scripts/check-links.ts fails when a sidebar link is not a page.

/**
 * Turns text into one URL path segment: Latin diacritics folded away, `&` read as "and", apostrophes
 * dropped, everything that is not a letter or digit collapsed to single hyphens. Letters and digits
 * of every script are kept.
 */
export function slugifySegment(text: string): string {
  return text
    .normalize("NFKD")
    .replaceAll(/(?<=\p{Script=Latin})\p{M}+/gu, "")
    .normalize("NFC")
    .replaceAll("&", " and ")
    .replaceAll(/['’]/g, "")
    .toLowerCase()
    .replaceAll(/[^\p{L}\p{M}\p{N}]+/gu, "-")
    .replaceAll(/^-+|-+$/g, "");
}

/** {@link slugifySegment} applied to each `/`-separated part, so a path keeps its depth. */
export function slugifyPath(text: string): string {
  return text
    .split("/")
    .map((part) => slugifySegment(part))
    .filter((part) => part !== "")
    .join("/");
}

/** "getting_started" and "Install-Steps" read as "Getting Started" and "Install Steps". */
export function humanize(name: string): string {
  const spaced = name.replaceAll(/[_-]+/g, " ").replaceAll(/\s+/g, " ").trim();
  if (spaced === "") return name;
  // Leave names that already have capitals (or digits first) alone: "OAuth Setup", "2FA".
  if (/[A-Z]/.test(spaced)) return spaced;
  return spaced.replaceAll(
    /(^|\s)(\p{L})/gu,
    (_m, space: string, letter: string) => space + letter.toUpperCase(),
  );
}
