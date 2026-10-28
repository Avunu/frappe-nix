---
title: The menu
description: "bench ui, the dev shell's interactive menu: the stack and its logs, every bench and Frappe command as a form, and a store for Frappe apps."
order: 2
tags: [cli, apps, tui]
updated: 2026-10-28
---

`bench ui` opens the dev shell's menu. It is the [`bench` wrapper](commands.md)'s own subcommand; a bare `bench` is still bench's help.

The menu changes nothing itself. Everything it does is a `bench …` command line, shown before it runs and streamed while it does, so what the menu does and what you would type are the same thing. `c` in a confirmation copies the command line.

| Key | Tab | What it shows |
| --- | --- | --- |
| `1` | Home | The shell's banner, whether the stack is up, the site's URL (the leased port; `o` opens it in the browser), and the everyday actions: migrate, build, the console, update. |
| `2` | Stack | `devenv up`'s processes and the selected one's log. `r`, `s`, `x` restart, start and stop it. `u` starts the stack in the background, `d` stops it, and `b` rebuilds: stops it, re-evaluates the shell and starts it again. The stack outlives the menu. |
| `3` | Commands | frappe-nix's own commands and every Frappe command of the bench's apps (listed once per environment, in the bench's Python, then cached), as a tree (`/` filters). The selected command is a form for its options. |
| `4` | Store | In a bench: the Frappe apps a bench can add. An app repository has no store: its siblings are flake inputs ([Apps in a bench](apps.md)). |

`ctrl+p` searches every action and command at once.

## The store

The catalog is frappe-nix-tools' `frappe-apps.json`: the marketplace registry's apps, with each release's Frappe range and the apps it needs, and the frappe organisation's apps that are not listed (webshop, newsletter, …). `frappe-nix apps-catalog sync` rewrites it, and a weekly workflow opens a pull request when it changes.

For the bench's Frappe (`apps/frappe`'s version) each app gets a branch: its `version-<n>` branch; else the branch of a registry release whose range holds the version; else, on a develop bench, `develop`. An app with none of these is listed, dimmed, with the branches it has.

- Type to search names, titles, publishers, categories and descriptions; the left column filters by publisher, the select by category (`Official` is the frappe organisation's).
- `space` picks the highlighted app; an installed one (`✓`, in `sites/apps.txt`) is picked for removal.
- The line at the bottom says which apps the picks need, which are added first.
- `a` shows what will run: `bench get-app --branch <branch> <app>` per app, and `bench uninstall-app` then `bench remove-app` per removal. Run it, and the menu offers **Rebuild & restart**: the stack restarts from a re-evaluated shell, and its apps-reconcile task installs the new apps on the site.
- **Add from a Git URL** takes anything `bench get-app` does.

The detail pane shows the app's listing and its README from GitHub when the network allows.
