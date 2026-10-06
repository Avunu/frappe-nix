import { expect, test } from "bun:test";
import { humanize, slugifyPath, slugifySegment } from "./lib/slug.ts";

test("slugs match the Jx route templates", () => {
  expect(slugifySegment("Git & Dev Tools")).toBe("git-and-dev-tools");
  expect(slugifySegment("Getting Started")).toBe("getting-started");
  expect(slugifySegment("What's new?")).toBe("whats-new");
  expect(slugifySegment("Café Résumé")).toBe("cafe-resume");
  expect(slugifySegment("  --odd__name--  ")).toBe("odd-name");
  expect(slugifySegment("日本語 docs")).toBe("日本語-docs");
  expect(slugifySegment("C++ notes")).toBe("c-notes");
});

test("a path keeps its depth and drops empty parts", () => {
  expect(slugifyPath("Guides/Install Steps")).toBe("guides/install-steps");
  expect(slugifyPath("a//b/")).toBe("a/b");
  expect(slugifyPath("")).toBe("");
});

test("humanize turns file names into titles and leaves names that already have capitals", () => {
  expect(humanize("getting_started")).toBe("Getting Started");
  expect(humanize("install-steps")).toBe("Install Steps");
  expect(humanize("OAuth Setup")).toBe("OAuth Setup");
  expect(humanize("odoo-nix")).toBe("Odoo Nix");
});
