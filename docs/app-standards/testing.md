---
title: Testing
description: "frappe-test: the app's tests with coverage, the whitelist and hook test map, the composition check and CI mode."
order: 3
tags: [app-standards, testing, coverage, frappe-test]
updated: 2026-10-28
---

`frappe-test` runs an app's tests the way CI's `ci / test` job does, from the app's dev shell. It brings the bench up, prepares a site, runs the tests with coverage measured on the app alone, and then checks what the coverage cannot: that every whitelisted function and hook target ran, and that the app's controller extensions compose with other apps'. The same command, with `--ci`, is the whole test job.

```bash
frappe-test --reuse-site    # the tests, the coverage gate, the testmap, the composition check, on the dev site as it is
frappe-test --recreate-site # the same on a fresh site: drops the site's database first
frappe-test --ci            # the stages the app's configuration turns on: what CI runs
frappe-test --reuse-site --keep-up   # keep the site and the bench between runs
frappe-test --module my_app.api.test_api   # one module (skips the coverage gate and the testmap)
frappe-test --down          # stop a bench --keep-up left running
```

It is a dev-shell command for every app in app mode, whether or not it has opted in to the app standards. The reports land in `.dev-dist/test/` (`--out` moves them).

> [!WARNING]
> The site is the dev shell's own (`$FRAPPE_SITE`) unless you pass `--site`. Recreating it runs `bench new-site --force`, which **drops the site's database** and sets Administrator's password to `$FRAPPE_NIX_TEST_ADMIN_PASSWORD` (`admin` by default). So when the site exists, `frappe-test` without `--ci` does nothing until you choose: `--reuse-site` tests on it as it is, `--recreate-site` drops and recreates it, and on a terminal it asks. `--ci` recreates it, as CI's fresh runner needs.

## What it runs

Every stage after the tests runs even when an earlier one failed, so one run reports everything. The exit status is the verdict of the first stage that failed, in this order.

| Stage | What | Fails with |
|---|---|---|
| up | `devenv up -D` unless the bench is already up (stopped again afterwards unless `--keep-up`). Waits up to 300 s (or `FRAPPE_NIX_UP_TIMEOUT` seconds, for a host that is slow by design) for MariaDB and for the web port to answer as this bench (its `/__frappe_nix/bench` identity, so another bench's nginx on the same port does not count); a failed `devenv up` (a taken port under `ports.strict`, say) fails at once. The leased ports are copied to `ports.json` in the output directory, and a web port that never answers is reported with `frappe-nix-ports who`. | 10 |
| site | `bench provision` for the `--site`, which recreates an existing one (`bench new-site --force`: its database is dropped), unless `--reuse-site` is given and the site exists; then `allow_tests`, then the `[tool.frappe-nix.tests] setup` steps (without the guards `devguard-disable` names, see [Development guards in a test run](#development-guards-in-a-test-run)). | 10 |
| tests | `coverage run … frappe … run-tests --app <app>`. | 1 |
| coverage | `coverage report` against `[tool.coverage.report] fail_under`, and the upward ratchet. | 2 |
| testmap | Every whitelisted function and hook target ran, or is exempt. | 3 |
| composition | Every controller the app extends composes in any app order. | 4 |
| ty | `uv run --frozen --project tools ty check` against the bench's interpreter (`--ty`). | 5 |
| nix-lint | `nixfmt --check`, `statix`, `deadnix` over `flake.nix` and `nix/*.nix` (`--nix-lint`). | 6 |
| shell checks | Each `[tool.frappe-nix] shell-checks` command, which must leave the tree as it found it (`--shell-checks`). | 7 |

A usage error (an unknown flag, no app, no site, an existing site with neither `--reuse-site` nor `--recreate-site` outside `--ci`) exits 64, and running it outside the app dev shell exits 10, so neither reads as a verdict.

`--ci` writes a JUnit report to `.dev-dist/test/junit.xml`, appends the summary to `$GITHUB_STEP_SUMMARY`, and runs exactly the stages the app's resolved configuration (its profile and `[tool.frappe-nix]`, see [Profiles](profiles.md)) turns on:

| Stages | Module that turns them on |
|---|---|
| up, site, tests | `tests` |
| coverage | `tests`, with `tests.coverage.enable` |
| testmap | `tests`, with `tests.testmap` |
| composition | `tests`, with `tests.composition` |
| ty | `python-types` |
| nix-lint | `nix-lint` |
| shell checks | a non-empty `shell-checks` |

With the `tests` module off, `--ci` starts no bench and skips the first six stages; ty, the Nix linters and the shell checks still run when theirs are on. A skipped stage is `skipped` in the report. A flag on the command line wins over the configuration (`frappe-test --ci --no-testmap`). Without `--ci`, `frappe-test` runs the tests, the coverage gate, the testmap and the composition check whatever the modules say, and the other three only when asked.

An app without `[tool.frappe-nix]` has no configuration to read: `--ci` runs the `recommended` profile's stages (ty only once a `tools/pyproject.toml` exists), with the default test setup and no shell checks. `frappe-nix test-plan` prints what `frappe-test` read, as JSON.

### Test setup

A fresh site sends every desk route to the setup wizard, and erpnext's tests need its test records. `[tool.frappe-nix.tests] setup` lists what runs before the app's tests, each step either `module:<dotted test module>` (run with `bench run-tests --module`, without coverage) or `execute:<dotted callable>` (`bench execute`). When the list is empty the default is `module:erpnext.tests.bootstrap_test_data` if erpnext is a sibling, and `execute:frappe.utils.install.complete_setup_wizard` otherwise.

```toml
[tool.frappe-nix.tests]
setup = ["execute:frappe.utils.install.complete_setup_wizard", "execute:my_app.tests.fixtures.create"]
```

### Development guards in a test run

The dev shell's [guard rails](../development/guard-rails.md) are on in `frappe-test` too. Most apps never notice, but an app that implements the very thing a guard intercepts cannot test itself under that guard. An email transport is the usual case: the `mail` guard removes `validate`, the SMTP and IMAP methods and the `override_email_send` hook from every Email Account and Email Domain extension, which is the whole of such an app. `devguard-disable` names the guards that the test run goes without:

```toml
[tool.frappe-nix.tests]
# This app is a mail transport: the mail guard strips the extensions its tests exercise.
devguard-disable = ["mail"]
```

`frappe-test` adds them to `FRAPPE_DEVGUARD_DISABLE` for the processes that run the app's tests: the setup steps, `run-tests`, the testmap and the composition check. It says so when it starts. The dev shell, `devenv up` and the bench `frappe-test` starts (web server, workers, scheduler) keep every guard, so working on the app by hand stays fully guarded. `egress` cannot be listed: it is what keeps a relaxed test run on this machine, so a test that would reach a real mail server or API still fails at the socket. The key is app-only; a profile cannot relax a guard for every app it covers.

## Coverage

Coverage is measured by `frappe-test` itself, never by frappe's `run-tests --coverage`. In app mode the bench lives inside the repository (`.frappe-nix/bench`), and frappe's own option measures everything under the app's directory, frappe and erpnext included. `frappe-test` runs the tests under `coverage run --source=<repo>/<app>`, which is the package's real directory, so nothing of frappe's or a sibling's is ever counted. `coverage.json`, `coverage.xml` and the per-module table use repository-relative paths.

The gate reads `[tool.coverage.report] fail_under` from the app's `pyproject.toml`. It also only moves up: while `fail_under` is below `tests.coverage.target` (80 in the built-in profiles), a total `tests.coverage.raise-margin` points (2) or more above it fails with the value to raise it to, never more than the target (`coverage is 91.4; raise [tool.coverage.report] fail_under to 80`). After the raise the total is within the margin, so the rule settles; `raise-margin = 0` turns it off. The PR that adds the tests carries the raise. Without a `fail_under` (an app that has not opted in, whose `pyproject.toml` sync never seeded) there is nothing to raise, so the ratchet only says so and the stage passes.

```toml
[tool.frappe-nix.tests.coverage]
target = 90          # ask for fail_under up to 90
raise-margin = 5     # once coverage is 5 points past it
```

`[tool.coverage.run] omit` and `[[tool.frappe-nix.coverage-omit]]` exclude files; `source` and `relative_files` belong to `frappe-test` and are refused by sync.

## The testmap

Coverage counts lines; the testmap asks a sharper question. Every one of these targets must have at least one line of its body run by the tests:

- **T1** every `@frappe.whitelist()` function or method under the package, outside `tests/`, `test_*.py` and `patches/`;
- **T2** every `scheduler_events` entry, `cron` included;
- **T3** every `doc_events` handler;
- **T4** every method an `extend_doctype_class` or `override_doctype_class` class defines itself (not the inherited ones, `__init__` included, other `_` names skipped);
- **T5** `override_whitelisted_methods`, `permission_query_conditions` and `has_permission` targets;
- **T6** the callable-valued hooks: `auth_hooks`, `before_request`, `after_request`, `on_session_creation`, `on_login`, `on_logout`, `boot_session`, `jinja` methods and filters, `additional_timeline_content`, `website_context` callables and `/api/method/` URLs, and the install and migrate hooks.

A function whose body shares its `def` line (`def ping(): return "pong"`) has no body line of its own: coverage marks that line run when the module is imported, so it would always count as tested. The testmap reports it untested instead, saying why; give the body a line of its own, or an exemption.

The hooks are read evaluated (`frappe.get_hooks(app_name=…)` on the connected site), so a hook defined under `if frappe_version >= 16:` counts. A module that fails to import is reported, with the error, rather than stopping the check. The install and migrate hooks (`after_install`, `after_migrate`, …) run before coverage starts, so a test calls them directly, or they get an exemption.

An exemption is an entry in `[[tool.frappe-nix.untested]]` with a reason. The list only shrinks: an exemption whose target is now tested, or no longer exists, fails as stale.

```toml
[[tool.frappe-nix.untested]]
target = "my_app.api.legacy"
reason = "Removed in 16.2; kept for old kiosk bundles"
```

`frappe-nix testmap` is the stage on its own; it needs a `coverage.json` from the same run.

## The composition check

An `extend_doctype_class` extension has to subclass the DocType's own controller. One that subclasses another app's extension composes only while that app is installed first, and on a site where the order differs the controller fails with `TypeError: Cannot create a consistent method resolution order`. The check builds each controller the app extends or overrides twice with frappe's own code, in the site's app order and with the app moved directly after frappe, and requires both to build with the app's class in the MRO. The report names the DocType and the order that failed.

## CI mode

`FRAPPE_NIX_CI=1` makes the dev shell fit for a CI job: shell entry skips the `node_modules` verify and install (about two minutes on a cold runner) and prints one line, `frappe-nix: CI mode (<bench>, site <site>)`, instead of the banner, and it exports what `devenv up` needs from a script. It is read when the shell starts, so the same shell serves both. The tests, `ty` and `run-tests` need no `node_modules`; `bench build` and screenshots do, so the jobs that run those leave it unset.

```bash
FRAPPE_NIX_CI=1 nix develop --no-pure-eval -c frappe-test --ci
```

## Several checkouts at once

Each checkout of an app gets its own database, Redis and sockets. Its TCP ports, nginx's and Mailpit's, are leased when `devenv up` starts, so any number of checkouts run side by side, CI jobs of one app on one runner host included: each prefers the ports [`ports.offset`](../reference/dev-shell-options.md#ports-and-sockets) gives it and moves past one another bench holds. The offset is a hash of the bench name in the primary checkout, and, in an app that opted in to the app standards, of the name and the path in a linked worktree (`git worktree add`), so two worktrees rarely want the same port in the first place. `FRAPPE_NIX_PORT_OFFSET=<0-899>` picks an offset by hand, and `FRAPPE_NIX_STRICT_PORTS=1` (or `ports.strict = true`) makes `devenv up` refuse a taken port, naming its holder, rather than move. `frappe-nix-ports` prints the ports in force. See [The development shell](../development/README.md#ports-and-sockets).

frappe-nix's own `selftest-runtime` workflow tests this with a `parallel` leg: two copies of its fixture app, under one name, run `frappe-test --ci` at the same moment, on the same host as the other legs' benches of that name. Each must lease distinct ports, answer on them as itself, hand devguard its own Mailpit and catch its own mail; a port held by a process outside frappe-nix must be moved past.

## frappe-test-report.json

The run's verdicts, for CI and for tools: the tests run and failed, the coverage total, `fail_under` and the per-module figures, the testmap counts, the composition results, the `ty` diagnostic and `ty: ignore` counts, each stage's verdict and the exit status. Its JSON Schema is `schema/frappe-test-report.schema.json` in frappe-nix-tools' data (`frappe-nix data-path schema/frappe-test-report.schema.json`). `summary.md` is the same as markdown.
