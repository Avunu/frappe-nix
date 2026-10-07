---
title: Testing
description: "frappe-test: the app's tests with coverage, the whitelist and hook test map, the composition check and CI mode."
order: 2
tags: [ironclad, testing, coverage, frappe-test]
updated: 2026-10-07
---

`frappe-test` runs an app's tests the way CI's `ci / test` job does, from the app's dev shell. It brings the bench up, prepares a site, runs the tests with coverage measured on the app alone, and then checks what the coverage cannot: that every whitelisted function and hook target ran, and that the app's controller extensions compose with other apps'. The same command, with `--ci`, is the whole test job.

```bash
frappe-test                 # the tests, the coverage gate, the testmap, the composition check
frappe-test --ci            # plus ty, the Nix linters and the shell checks: what CI runs
frappe-test --reuse-site --keep-up   # keep the site and the bench between runs
frappe-test --module my_app.api.test_api   # one module (skips the coverage gate and the testmap)
frappe-test --down          # stop a bench --keep-up left running
```

It is a dev-shell command in app mode. The reports land in `.dev-dist/test/` (`--out` moves them).

## What it runs

Every stage after the tests runs even when an earlier one failed, so one run reports everything. The exit status is the verdict of the first stage that failed, in this order.

| Stage | What | Fails with |
|---|---|---|
| up | `devenv up -D` unless the bench is already up (stopped again afterwards unless `--keep-up`). Waits up to 300 s for MariaDB and the web port. | 10 |
| site | `provision-site` (unless `--reuse-site` and the site exists), `allow_tests`, then the `[tool.ironclad.test] setup` steps. | 10 |
| tests | `coverage run … frappe … run-tests --app <app>`. | 1 |
| coverage | `coverage report` against `[tool.coverage.report] fail_under`, and the upward ratchet. | 2 |
| testmap | Every whitelisted function and hook target ran, or is exempt. | 3 |
| composition | Every controller the app extends composes in any app order. | 4 |
| ty | `uv run --frozen --project tools ty check` against the bench's interpreter (`--ty`). | 5 |
| nix-lint | `nixfmt --check`, `statix`, `deadnix` over `flake.nix` and `nix/*.nix` (`--nix-lint`). | 6 |
| shell checks | Each `[tool.ironclad] shell-checks` command, which must leave the tree as it found it (`--shell-checks`). | 7 |

`--ci` turns on the last three, writes a JUnit report to `.dev-dist/test/junit.xml`, and appends the summary to `$GITHUB_STEP_SUMMARY`.

### Test setup

A fresh site sends every desk route to the setup wizard, and erpnext's tests need its test records. `[tool.ironclad.test] setup` lists what runs before the app's tests, each step either `module:<dotted test module>` (run with `bench run-tests --module`, without coverage) or `execute:<dotted callable>` (`bench execute`). When the list is empty the default is `module:erpnext.tests.bootstrap_test_data` if erpnext is a sibling, and `execute:frappe.utils.install.complete_setup_wizard` otherwise.

```toml
[tool.ironclad.test]
setup = ["execute:frappe.utils.install.complete_setup_wizard", "execute:my_app.tests.fixtures.create"]
```

## Coverage

Coverage is measured by `frappe-test` itself, never by frappe's `run-tests --coverage`. In app mode the bench lives inside the repository (`.frappe-nix/bench`), and frappe's own option measures everything under the app's directory, frappe and erpnext included. `frappe-test` runs the tests under `coverage run --source=<repo>/<app>`, which is the package's real directory, so nothing of frappe's or a sibling's is ever counted. `coverage.json`, `coverage.xml` and the per-module table use repository-relative paths.

The gate reads `[tool.coverage.report] fail_under` from the app's `pyproject.toml`. It also only moves up: while `fail_under` is below 80, a total 2 points or more above it fails with the value to raise it to (`coverage is 91.4; raise [tool.coverage.report] fail_under to 80`). The PR that adds the tests carries the raise. `[tool.coverage.run] omit` and `[[tool.ironclad.coverage-omit]]` exclude files; `source` and `relative_files` belong to `frappe-test` and are refused by sync.

## The testmap

Coverage counts lines; the testmap asks a sharper question. Every one of these targets must have at least one line of its body run by the tests:

- **T1** every `@frappe.whitelist()` function or method under the package, outside `tests/`, `test_*.py` and `patches/`;
- **T2** every `scheduler_events` entry, `cron` included;
- **T3** every `doc_events` handler;
- **T4** every method an `extend_doctype_class` or `override_doctype_class` class defines itself (not the inherited ones, `__init__` included, other `_` names skipped);
- **T5** `override_whitelisted_methods`, `permission_query_conditions` and `has_permission` targets;
- **T6** the callable-valued hooks: `auth_hooks`, `before_request`, `after_request`, `on_session_creation`, `on_login`, `on_logout`, `boot_session`, `jinja` methods and filters, `additional_timeline_content`, `website_context` callables and `/api/method/` URLs, and the install and migrate hooks.

The hooks are read evaluated (`frappe.get_hooks(app_name=…)` on the connected site), so a hook defined under `if frappe_version >= 16:` counts. A module that fails to import is reported, with the error, rather than stopping the check. The install and migrate hooks (`after_install`, `after_migrate`, …) run before coverage starts, so a test calls them directly, or they get an exemption.

An exemption is an entry in `[[tool.ironclad.untested]]` with a reason. The list only shrinks: an exemption whose target is now tested, or no longer exists, fails as stale.

```toml
[[tool.ironclad.untested]]
target = "my_app.api.legacy"
reason = "Removed in 16.2; kept for old kiosk bundles"
```

`ironclad testmap` is the stage on its own; it needs a `coverage.json` from the same run.

## The composition check

An `extend_doctype_class` extension has to subclass the DocType's own controller. One that subclasses another app's extension composes only while that app is installed first, and on a site where the order differs the controller fails with `TypeError: Cannot create a consistent method resolution order`. The check builds each controller the app extends or overrides twice with frappe's own code, in the site's app order and with the app moved directly after frappe, and requires both to build with the app's class in the MRO. The report names the DocType and the order that failed.

## CI mode

`FRAPPE_NIX_CI=1` makes the dev shell fit for a CI job: shell entry skips the `node_modules` verify and install (about two minutes on a cold runner) and prints one line, `frappe-nix: CI mode (<bench>, site <site>)`, instead of the banner, and it exports what `devenv up` needs from a script. It is read when the shell starts, so the same shell serves both. The tests, `ty` and `run-tests` need no `node_modules`; `bench build` and screenshots do, so the jobs that run those leave it unset.

```bash
FRAPPE_NIX_CI=1 nix develop --no-pure-eval -c frappe-test --ci
```

## Several checkouts at once

Each checkout of an app gets its own database, Redis and sockets. Its TCP ports, nginx's and Mailpit's, come from [`ports.offset`](../reference/dev-shell-options.md#ports-and-sockets): a hash of the bench name in the primary checkout, and of the name and the path in a linked worktree (`git worktree add`), so two worktrees of one app run side by side. `FRAPPE_NIX_PORT_OFFSET=<0-899>` picks an offset by hand, and `devenv up` stops with the port's name when one is already taken. See [The development shell](../development/README.md#ports-and-sockets).

## ironclad-report.json

The run's verdicts, for CI and for tools: the tests run and failed, the coverage total, `fail_under` and the per-module figures, the testmap counts, the composition results, the `ty` diagnostic and `ty: ignore` counts, each stage's verdict and the exit status. Its JSON Schema is `schema/ironclad-report.schema.json` in ironclad's data (`ironclad data-path schema/ironclad-report.schema.json`). `summary.md` is the same as markdown.
