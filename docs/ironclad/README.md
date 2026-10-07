---
title: Ironclad
description: "The Ironclad platform in frappe-nix, which gives every Frappe app the same managed files, CI gates, release flow and marketplace checks."
nav_title: Ironclad
order: 7
tags: [ironclad, apps, ci, releases, marketplace]
updated: 2026-10-06
---

Ironclad is the part of frappe-nix that makes every Avunu Frappe app look and behave the same: one set of managed files kept in step by `frappe-init --sync`, one set of required CI checks, one release flow, and the checks the Frappe marketplace runs. It is driven by the `ironclad` command, which is on `PATH` in an app's dev shell.

The [interface spec](spec.md) is the contract the pieces are built to. The pages below describe each piece as it lands.

- [Managed files](managed-files.md): The files frappe-nix keeps in every app, the [tool.ironclad] parameters, and frappe-init --sync and --check.
- [Testing](testing.md): frappe-test: the app's tests with coverage, the whitelist and hook test map, the composition check and CI mode.
- [App assets](assets.md): How an app's Vite outputs are registered on every bench, and how frontends are named and built.
- [CI](ci.md): The reusable workflows behind ci / lint, typecheck, test, marketplace and pr-policy, and the files that call them.
- [GitHub settings](github.md): The rulesets, repository settings and release flow every app gets, and how they are applied and audited.
- [Marketplace](marketplace.md): The listing file, the registry checks an app must pass and how a release reaches the Frappe marketplace.
- [Icons](icons.md): The symbolic icon and logo tile every app ships, and the checks frappe-icon runs on them.
- [Screenshots](screenshots.md): Repeatable screenshots from a demo site: the screenshot spec, frappe-demo and frappe-shots.
- [Versioning](versioning.md): How an app pins frappe-nix, how a frappe-nix release reaches every app, and what each version number promises.
- [Renaming an app](rename.md): frappe-rename-app: renaming an app's package in the code and on existing sites.
- [Interface spec](spec.md): the full contract, section by section.
