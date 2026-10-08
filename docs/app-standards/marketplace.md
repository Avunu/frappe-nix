---
title: Marketplace
description: "The listing file, the registry checks an app must pass and how a release reaches the Frappe marketplace."
order: 7
tags: [app-standards, marketplace, registry]
updated: 2026-10-07
---

The marketplace tools make an app ready for the Frappe marketplace registry and keep it that way. They are off in the built-in profiles: an app or an organisation profile turns them on with the `listing` module (and `readme`, `icons`, `screenshots` and `demo` for the parts those name). Everything an organisation decides, such as the publisher, the e-mail, the licence, the URL patterns and the registry fork, comes from the profile's `[org]` and `[listing]` tables, never from frappe-nix.

```toml
# pyproject.toml, or an org profile's profile.toml
[tool.frappe-nix.listing]
enable = true
publish = false          # true: frappe-listing registry may open registry pull requests
registry-fork = ""       # "<owner>/marketplace", required when publish is on
getapp-check = true      # L9, pilot's get-app validator
```

`listing` needs `metadata`. An app that is not listed keeps `listing` on with `publish = false` and no `listing.toml`: the registry-readiness rules still run on every pull request (`ci / marketplace`).

## The files

| File | Owner | What |
|---|---|---|
| `marketplace/listing.toml` | the app | The listing: title, tagline, type, category, URLs. `frappe-init --sync --init-listing` seeds it once from the profile's URL templates. |
| `marketplace/semgrep-baseline.json` | the app (seeded) | The registry semgrep findings the app carries, by count. Sync seeds it empty. |
| `README.md` | the app, except six blocks | The `readme` module renders the blocks between `<!-- frappe-nix:begin <name> -->` and `<!-- frappe-nix:end <name> -->`. |
| `marketplace/screenshots.ts` | the app | The screenshot spec ([Screenshots](screenshots.md)). |
| `marketplace/shots.d.ts` | sync | The spec's types, kept in step with frappe-nix. |
| `<app>/public/images/<app>-symbolic.svg`, `<app>-logo.svg` | the app | The icon pair ([Icons](icons.md)). |

`listing.toml`:

```toml
schema = 1
title = "Example Board for Frappe"         # at most 40 characters; marks only as "… for X" or "… via X"
tagline = "Kanban boards for any Frappe doctype, with swimlanes and WIP limits"   # 40 to 80, no final period
type = "extension"                         # application | extension | integration | utility
category = "Extensions"                    # Applications | Compliance | Developer Tools | Extensions | Integrations | Utilities
categories = ["Productivity"]
website = "https://example.org/apps/example_board/"
documentation = "https://example-board.docs.example.org/"
apps_screen = false                        # true needs [apps_screen_entry] route and has_permission
```

## frappe-listing check

`frappe-listing check` (the same as `frappe-nix listing check`) runs the registry's rules on the app in the current directory. CI's `ci / marketplace` job runs it with `--format github`; the JSON report lands in `.dev-dist/marketplace/report.json`.

| Rule | What | Severity |
|---|---|---|
| L1 | `listing.toml` against its schema | error |
| L2 | `hooks.py` against the listing and the org values (`app_publisher`, `app_email`, `app_license`); no `app_logo_url` or `bench new-app` placeholders; `add_to_apps_screen` matches `apps_screen` | error |
| L3 | `[tool.bench.frappe-dependencies]`: exactly frappe and the required apps, each with its sibling's range, in the comma form | error |
| L4 | one version in the version block, `package.json` and the release-please manifest; with `--release --tag`, the tag's | error |
| L5 | no side effects in `__init__.py`; no tracked symlink leaving the repository | error |
| L6 | every `override_doctype_class` has a `[[tool.frappe-nix.override-doctype-class]]` reason, and the reverse | error |
| L7 | the registry's own semgrep check, from the pinned `frappe/marketplace`, against the baseline | error |
| L8 | the logo's structure (`frappe-icon check --structural`), with `icons` on | error |
| L9 | pilot's get-app validator, from the pinned `frappe/pilot`, on a bench that also has the app's dependency apps | error |
| L10 | `website` and `documentation` answer 200 over https (`--links-only`, `--release`) | error |
| L11 | every `<owner>/<repo>` required app is listed upstream (`--release`) | error |
| L12 | `website` and `documentation` follow the profile's URL templates | warning |

L1, L2, L8, L10 and L12 run only when `listing.toml` exists. With an org value unset, L2 asks only that the hook be set, and L12 has no pattern to compare. `--no-getapp` skips L9 for a quick local run. Exit codes: 0 pass (warnings allowed), 1 an error, 2 a configuration or parse error, 3 the environment (the network, a pin's hash, a pilot bench that could not be built).

### The semgrep baseline

L7 counts findings. Each one is keyed by its rule, its file and the SHA-1 of its first matched line, stripped, so line numbers can move without touching the baseline, and twelve identical `frappe.db.commit()` lines are one entry with `count = 12`:

```json
{
	"schema": 1,
	"marketplace_rev": "2dd4be42eec1bbedc16a91c95a2c7e7e8728df7e",
	"findings": [
		{
			"rule": "frappe-manual-commit",
			"path": "example_board/api.py",
			"line_sha1": "7d6d0a8c…",
			"count": 12,
			"reason": "Board moves commit per card on purpose"
		}
	]
}
```

A key found more often than its count is a new finding; found less often, the entry is stale and its count must come down. So the baseline only shrinks, and a release (`--release`) needs it empty. `frappe-listing check --write-baseline` records the findings found now, which is how an app adopting the standards starts; give each entry a reason.

### L9 and the dependency apps

The registry validates an app on a throwaway bench. An app that imports erpnext or hrms fails pilot's import check there unless those apps are on the bench too, so L9 builds the bench with frappe and every app in `[tool.bench.frappe-dependencies]` at the revisions the app's `flake.lock` pins, then runs each of pilot's checks on its own. It needs `uv` and, when the app imports something the bench lacks, `pkg-config` and the MySQL client headers (the dev shell has them; CI's marketplace job installs them).

## frappe-listing registry

`frappe-listing registry` publishes a release: it runs `check --release` on the release commit, rebuilds a branch `<fork owner>/<app>` of the registry fork on upstream `main`, adds every pending release with the registry's own `tools/add_release.py` (and, the first time or with `--onboard`, the app's `apps.json` entry), and opens or updates the pull request.

```bash
frappe-listing registry                      # a dry run: prints the diff, pushes nothing
frappe-listing registry --yes                # push to the fork and open the pull request
frappe-listing registry --tag v1.4.0 --yes   # a release other than the current __version__'s
frappe-listing registry --refresh --yes      # rebase the open pull request on upstream main
```

It needs `listing.publish` and `listing.registry-fork` (exit 2 otherwise), and nothing leaves the machine without `--yes`. The token is `GH_TOKEN`, or your `gh` login.

## The README blocks

With `readme` on (it needs `listing`), sync renders six blocks into `README.md`, in this order: `header` (logo, title, tagline, badges, the hero screenshot), `compatibility`, `installation`, `support`, `development` and `license`. Your own sections go between them. A missing or out-of-order marker is exit 2, and `frappe-listing readme --check` (or `frappe-init --check`) reports an edited block as drift. The blocks are written in the form the markdown formatter leaves alone, so oxfmt and sync never fight over them.

The values come from the profile: `org.license` and `org.copyright-holder` (required), `org.email`, `org.support-url`, `org.dev-docs-url`, `org.repo-url` (a template with `{repo}`, for an organisation off GitHub), `org.readme.badges` and `org.readme.links`, and `readme.install-ref`. Under `releases.branching = "main+tags"` the installation line names the released tag inside release-please's version markers, and `README.md` is a release-please extra file, so the release pull request moves it.

An organisation replaces a block's template with `templates/readme/<block>.md.j2` in its profile; the template gets the same context, plus `readme` with the values above already worked out.
