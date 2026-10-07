---
title: Renaming an app
description: "frappe-rename-app: renaming an app's package in the code and on existing sites."
order: 10
tags: [ironclad, rename, migration, frappe-rename-app]
updated: 2026-10-07
---

Renaming a Frappe app's package (`esign` to `esign_webforms`) touches two places that cannot change at the same moment: the app's code, and every site that has the app installed. `frappe-rename-app` does each half. The module names are kept, so every DocType, custom field, property setter, report, print format and workspace keeps pointing at a module the renamed package still declares, and no record moves.

## The code half

In the app's repository, on a clean tree:

```bash
frappe-rename-app code --from esign --to esign_webforms --dry-run   # the diff, nothing changed
frappe-rename-app code --from esign --to esign_webforms             # the same, staged
```

It:

1. moves the package (`git mv esign esign_webforms`), keeping every module folder and every `modules.txt` line, and renames each file under it whose name starts with `esign.` (the bundles `esign.desk.bundle.js`, `esign.control.bundle.css`, …), so the names `hooks.py` gives and the files agree;
2. rewrites the tracked text: dotted paths (`esign.esign.custom.web_form.accept`), Python imports, `/assets/esign/` and `/api/method/esign.` URLs, template paths (`esign/templates/…`), `patches.txt` line by line, `[project].name` and flit's module name in `pyproject.toml`, `package.json`'s `name`, `app_name`, the CI's `--app` and release-please's `package-name`;
3. bumps `modified` in every standard JSON whose content changed, or existing sites would never re-import it;
4. appends an `override_whitelisted_methods` shim to `hooks.py`, between `# ironclad:rename-shim-begin esign` and `# ironclad:rename-shim-end`, mapping each whitelisted function's old dotted path to its new one, for the callers you cannot update in the same release: cached bundles, webhooks, bookmarks. Keep it for at least one minor release.

A name like `esign.legacy.bundle.css` that names no module and no renamed file is left alone and listed: rewriting it would point at a file that does not exist. It also lists every other mention it kept on purpose: custom fieldnames, DocType names, Communication types, CSS classes, `localStorage` keys, JavaScript namespaces and module names. History (`CHANGELOG.md`) and lock files are not touched. Review the list, then commit.

It exits 1 on a dirty tree, and for a pair that is replaced rather than renamed (below).

## The site half

Once the renamed code is on a bench, a site still records the old name: in `installed_apps`, in `Module Def`, in the Patch Log, in the scheduled jobs. `bench migrate` fails at once on an installed app it cannot import, so the site half runs first:

```bash
frappe-rename-app --site erp.example.com esign=esign_webforms --dry-run --scan   # what it would change, and what else names esign
frappe-rename-app --site erp.example.com esign=esign_webforms --yes
bench --site erp.example.com migrate
```

In one transaction, it rewrites:

- the `installed_apps` global, keeping the order (which keeps every extension's MRO);
- `Module Def.app_name`, including custom modules placed in the app;
- the Patch Log, so no renamed patch runs again;
- `Scheduled Job Type.method` and `Scheduler Event.method` in place, so each job keeps its row, its `stopped` flag and its log;
- the columns that hold an app name: `Desktop Icon`, `Dock`, `Sidebar`, `Sidebar Item Group`, `Workspace Sidebar` and `Website Theme Ignore App` `.app`, `Notification Log.app`, `User.default_app`, `System Settings.default_app` and `User Invitation.app_name`;
- `esign.` dotted paths and `/assets/esign/` URLs in what a site's users write: report and server and client scripts, print formats, web forms and pages, web templates, custom HTML blocks, letter heads, website route redirects, number cards, DocType actions and navbar items.

After the commit it updates the `installed_apps` copy in `site_config.json` and drops the cached app and module maps. It refuses (exit 1) when both apps are installed, when the new app is not on the bench (`sites/apps.txt`), or when its `modules.txt` lacks a module the old app owns. A database error rolls everything back (exit 2). On a site without the old app it prints `esign not installed on <site>: nothing to do`, so it is safe to run on every deploy. `--scan` reports, read-only, every text column in every table that still names the old app, logs excepted.

## Where it runs

You rarely run the site half by hand.

- **Production.** `services.frappe.sites.<site>.renamedApps = { esign = "esign_webforms"; };` runs it in the site's migrate unit, right after maintenance mode goes on and before the online migrate and `bench migrate`, inside the same snapshot and rollback.
- **The dev shell.** `frappe-nix.renamedApps` does the same in `reconcile-apps`, which `devenv up` runs on `siteName` before anything starts, ahead of installing whatever `sites/apps.txt` names: installing the new app beside the renamed old one would fail on the `Module Def` they share.

Before the production deploy that ships a rename, pause the scheduler and let the job queues drain: a job already queued under an old dotted path fails once the code is gone. Run `--dry-run --scan` against a restored copy of the production database first and review what it lists.

## Replacing an app instead

Some apps are replaced by a new app rather than renamed: `jailbreak` becomes `data_steward`, which has its own module and settings DocType, so both can be installed at once. `frappe-rename-app code` refuses such a pair. Ship both apps, then:

- **Production.** `services.frappe.sites.<site>.replacedApps = { jailbreak = "data_steward"; };`: where the old app is installed, the migrate unit installs the new one (if needed) and uninstalls the old one, without a backup of its own (the pre-migrate snapshot is the backup), inside the snapshot and before migrating. Where the old app is not installed it does nothing.
- **The dev shell.** `frappe-nix.replacedApps` does the same in `reconcile-apps`.

The new app's `after_install` copies the old settings across. The old app leaves the bench only after every site has run the replacement, because uninstalling it needs its hooks.
