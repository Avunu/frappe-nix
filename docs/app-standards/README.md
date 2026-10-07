---
title: App standards and quality gates
description: "Opt-in app standards for Frappe apps in frappe-nix app mode: managed files, profiles, CI gates, releases and marketplace checks."
nav_title: App standards
order: 7
tags: [app-standards, apps, ci, releases, marketplace]
updated: 2026-10-07
---

The app standards are an opt-in part of frappe-nix app mode. An app that opts in gets one set of managed files kept in step by `frappe-init --sync`, the CI gates and release flow its profile enables, and the checks the Frappe marketplace runs. They are driven by the `frappe-nix` command, which is on `PATH` in an opted-in app's dev shell.

Nothing changes for an app that has not opted in: without a `[tool.frappe-nix]` table in its `pyproject.toml`, `frappe-init --app` and the dev shell behave exactly as they always have. An app opts in with a profile. The built-in `minimal` profile manages only the dev shell's files, `recommended` adds vendor-neutral lint, format, type, test, commit, release, Dependabot and CI standards, and an organisation can publish its own profile on top of either.

The [interface spec](spec.md) is the contract the pieces are built to. The pages below describe each piece as it lands.

- [Profiles](profiles.md): How an app opts in, the built-in minimal and recommended profiles, org profiles, and how [tool.frappe-nix] layers over them.
- [Managed files](managed-files.md): The files frappe-nix keeps in an opted-in app, the [tool.frappe-nix] parameters, and frappe-init --sync and --check.
- [Testing](testing.md): frappe-test: the app's tests with coverage, the whitelist and hook test map, the composition check and CI mode.
- [App assets](assets.md): How an app's Vite outputs are registered on every bench, and how frontends are named and built.
- [CI](ci.md): The gate commands and the reusable workflows behind ci / lint, typecheck, test, marketplace and pr-policy, and the files that call them.
- [GitHub settings](github.md): The optional rulesets and repository settings, the release flow, and how they are applied and audited.
- [Marketplace](marketplace.md): The listing file, the registry checks an app must pass and how a release reaches the Frappe marketplace.
- [Icons](icons.md): The symbolic icon and logo tile an app ships, and the checks frappe-icon runs on them.
- [Screenshots](screenshots.md): Repeatable screenshots from a demo site: the screenshot spec, frappe-demo and frappe-shots.
- [Versioning](versioning.md): How an app pins frappe-nix and its profile, how a release reaches every app, and what each version number promises.
- [Renaming an app](rename.md): frappe-rename-app: renaming an app's package in the code and on existing sites.
- [Interface spec](spec.md): the full contract, section by section.
