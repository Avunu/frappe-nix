// WCAG contrast check for the design tokens in project.json.
//
//   bun run check:contrast
//
// Resolves every colour token for the light theme and for the dark theme (the @--dark overrides),
// then tests the foreground and background pairs the components use: 4.5:1 for text, 3:1 for large
// text, focus rings and UI boundaries. Exits 1 when a pair is below its minimum.
//
// The pairs are a list (PAIRS) plus the search highlight, which is read from the component's own
// style (a colour mixed into transparent, so its contrast depends on the row behind it). A colour
// pair that is in no list is not checked: scripts that open the palette, the menus and the drawer
// and run axe in a real browser (verify/axe.ts) are what catches those.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ROOT } from "./lib/config.ts";

type Tokens = Record<string, string>;
export type Pair = [foreground: string, background: string, minimum: number, label: string];

const collect = (src: Record<string, unknown>): Tokens =>
  Object.fromEntries(
    Object.entries(src).filter(([k, v]) => k.startsWith("--") && typeof v === "string"),
  ) as Tokens;

export function themes(style: Record<string, unknown>): { light: Tokens; dark: Tokens } {
  const light = collect(style);
  return {
    light,
    dark: { ...light, ...collect((style["@--dark"] as Record<string, unknown>) ?? {}) },
  };
}

function resolve(name: string, tokens: Tokens, depth = 0): string {
  const raw = tokens[name];
  if (raw === undefined) throw new Error(`Unknown token ${name}`);
  const ref = /^var\((--[\w-]+)\)$/.exec(raw.trim());
  if (ref && depth < 8) return resolve(ref[1]!, tokens, depth + 1);
  return raw.trim();
}

type Rgba = [number, number, number, number];

export function parseColor(color: string): Rgba {
  const hex = /^#([0-9a-f]{6})$/i.exec(color);
  if (hex) {
    const n = Number.parseInt(hex[1]!, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 1];
  }
  const rgba = /^rgba?\(([^)]+)\)$/.exec(color);
  if (rgba) {
    const [r, g, b, a = "1"] = rgba[1]!.split(",").map((s) => s.trim());
    return [Number(r), Number(g), Number(b), Number(a)];
  }
  throw new Error(`Cannot parse colour ${color}`);
}

const over = (fg: Rgba, bg: Rgba): [number, number, number] =>
  [0, 1, 2].map((i) => fg[i]! * fg[3] + bg[i]! * (1 - fg[3])) as [number, number, number];

function luminance([r, g, b]: [number, number, number]): number {
  const f = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

/** The contrast ratio of a foreground over a background, both over the page colour. */
export function ratio(fg: string, bg: string, page: string): number {
  const base = parseColor(page);
  const back = over(parseColor(bg), [base[0], base[1], base[2], 1]);
  const front = over(parseColor(fg), [...back, 1]);
  const [hi, lo] = [luminance(front), luminance(back)].sort((a, b) => b - a) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

const T = 4.5;
const L = 3;

export const PAIRS: Pair[] = (() => {
  const pairs: Pair[] = [];
  for (const bg of [
    "--color-bg",
    "--color-bg-sand",
    "--color-bg-lavender",
    "--color-surface",
    "--color-surface-raised",
    "--color-surface-muted",
    "--color-surface-subtle",
  ]) {
    pairs.push(["--color-text", bg, T, `text on ${bg}`]);
    pairs.push(["--color-text-muted", bg, T, `muted text on ${bg}`]);
    pairs.push(["--color-text-caption", bg, T, `caption on ${bg}`]);
    pairs.push(["--color-link", bg, T, `link on ${bg}`]);
    pairs.push(["--color-link-hover", bg, T, `link hover on ${bg}`]);
    pairs.push(["--color-focus", bg, L, `focus ring on ${bg}`]);
  }
  pairs.push(
    ["--color-text-placeholder", "--color-surface", T, "search placeholder"],
    ["--color-on-action", "--color-action", T, "primary button"],
    ["--color-on-action", "--color-action-hover", T, "primary button hover"],
    ["--color-on-action-soft", "--color-action-soft", T, "secondary button"],
    ["--color-on-tint", "--color-tint", T, "current sidebar item, tags, switcher current project"],
    ["--color-text", "--color-code-bg", T, "inline code"],
    ["--color-rule-strong", "--color-bg", L, "table header rule"],
  );
  for (const kind of ["note", "tip", "important", "warning", "caution"]) {
    pairs.push([`--callout-${kind}-fg`, `--callout-${kind}-bg`, T, `${kind} callout title`]);
    pairs.push(["--color-text", `--callout-${kind}-bg`, T, `${kind} callout body`]);
    pairs.push(["--color-link", `--callout-${kind}-bg`, T, `link inside a ${kind} callout`]);
  }
  for (const bg of [
    "--color-night",
    "--color-night-surface",
    "--color-night-surface-2",
    "--color-night-surface-3",
  ]) {
    pairs.push(["--color-night-text", bg, T, `night text on ${bg}`]);
    pairs.push(["--color-night-muted", bg, T, `night muted on ${bg}`]);
    pairs.push(["--color-night-link", bg, T, `night link on ${bg}`]);
    pairs.push(["--color-night-focus", bg, L, `night focus ring on ${bg}`]);
  }
  return pairs;
})();

/** A tinted highlight: `color-mix(in srgb, var(--token) 22%, transparent)`. */
export interface Highlight {
  token: string;
  percent: number;
  /** The text colours it can sit behind: the rule's own `color`, or (inherited) the row's title and excerpt colours. */
  text: string[];
}

/** Reads the highlight out of a component style such as docs-search's `& .hl` rule. */
export function highlightOf(rule: Record<string, unknown> | undefined): Highlight | null {
  const value = typeof rule?.backgroundColor === "string" ? rule.backgroundColor : "";
  const m = /^color-mix\(in srgb,\s*var\((--[\w-]+)\)\s+(\d+(?:\.\d+)?)%,\s*transparent\)$/.exec(
    value.trim(),
  );
  if (!m) return null;
  const own = /^var\((--[\w-]+)\)$/.exec(String(rule?.color ?? "").trim());
  return {
    token: m[1]!,
    percent: Number(m[2]),
    text: own ? [own[1]!] : ["--color-text", "--color-text-caption"],
  };
}

/** `foreground` laid over `background` at `alpha`, as a hex colour. */
function blend(foreground: string, alpha: number, background: string): string {
  const [front, back] = [parseColor(foreground), parseColor(background)];
  const mixed = [0, 1, 2].map((i) =>
    Math.round(front[i]! * alpha + back[i]! * (1 - alpha))
      .toString(16)
      .padStart(2, "0"),
  );
  return `#${mixed.join("")}`;
}

export interface Failure {
  theme: "light" | "dark";
  label: string;
  ratio: number;
  minimum: number;
}

export function contrastFailures(
  style: Record<string, unknown>,
  highlight: Highlight | null = null,
): {
  checked: number;
  failures: Failure[];
} {
  const { light, dark } = themes(style);
  const failures: Failure[] = [];
  let checked = 0;
  for (const [theme, tokens] of [
    ["light", light],
    ["dark", dark],
  ] as const) {
    const page = resolve("--color-bg", tokens);
    for (const [fg, bg, minimum, label] of PAIRS) {
      // Night tokens are the same in both themes: test them once.
      if (theme === "dark" && label.startsWith("night ")) continue;
      checked++;
      const value = ratio(resolve(fg, tokens), resolve(bg, tokens), page);
      if (value < minimum)
        failures.push({ theme, label, ratio: Math.round(value * 100) / 100, minimum });
    }
    if (highlight) {
      // The mark sits on the palette's surface, and on the surface of a hovered or active row.
      for (const row of ["--color-surface", "--color-surface-muted"]) {
        const background = blend(
          resolve(highlight.token, tokens),
          highlight.percent / 100,
          resolve(row, tokens),
        );
        for (const text of highlight.text) {
          const label = `search highlight (${text}) on ${row}`;
          const value = ratio(resolve(text, tokens), background, page);
          if (value < T)
            failures.push({ theme, label, ratio: Math.round(value * 100) / 100, minimum: T });
          checked++;
        }
      }
    }
  }
  return { checked, failures };
}

if (import.meta.main) {
  const project = JSON.parse(readFileSync(join(ROOT, "project.json"), "utf8")) as {
    style: Record<string, unknown>;
  };
  const search = JSON.parse(readFileSync(join(ROOT, "components", "docs-search.json"), "utf8")) as {
    style: Record<string, Record<string, unknown>>;
  };
  const { checked, failures } = contrastFailures(project.style, highlightOf(search.style["& .hl"]));
  for (const f of failures)
    console.error(`FAIL ${f.theme}: ${f.label} is ${f.ratio}:1, needs ${f.minimum}:1`);
  console.log(`check-contrast: ${checked} pair(s) checked, ${failures.length} below the minimum`);
  process.exit(failures.length === 0 ? 0 : 1);
}
