---
title: Getting started
description: Scaffold a new Frappe bench with frappe-nix, start the development shell and create your first site.
order: 2
tags: [quick-start, devenv, bench]
updated: 2026-10-06
---

This page takes you from an empty directory to a running Frappe site. It uses the new-bench path. If you already have a bench or a single app, pick the matching mode in [Choose a mode](scaffolding/README.md) and come back for the shell steps.

## Prerequisites

You need:

- [Nix](https://nixos.org/download.html) with flakes enabled.
- [direnv](https://direnv.net/), so the shell loads when you enter the directory. Without it, `nix develop --no-pure-eval` does the same job by hand.
- git, because apps are tracked as git submodules or as flake inputs.

The generated flake declares the `devenv.cachix.org` binary cache, so Nix may ask you to trust it the first time you enter the shell.

> [!IMPORTANT]
> The `--no-pure-eval` flag appears in every entry point: the generated `.envrc` is the single line `use flake . --no-pure-eval`, and the manual command is `nix develop --no-pure-eval`. Keep the flag when you load the shell by hand.

## 1. Scaffold a bench

Run the scaffolder from the directory where the bench should live. With a terminal and no flags it prompts you (using [gum](https://github.com/charmbracelet/gum)) for a Frappe version, a set of apps and a directory name.

```bash
nix run github:Avunu/frappe-nix
```

For a scripted run, pass everything as flags. Without a terminal, `--frappe-version` is required.

```bash
nix run github:Avunu/frappe-nix -- \
  --frappe-version version-15 --apps erpnext,hrms --name mybench mybench
```

The scaffolder writes the wrapper `flake.nix`, adds `frappe` and your apps as git submodules on the branch that matches the version you chose, and runs `uv lock`. See [Create a new bench](scaffolding/new-bench.md) for the presets, the flags and the files it writes.

## 2. Enter the shell

```bash
cd mybench
direnv allow
```

Without direnv, run `nix develop --no-pure-eval` instead. The first entry builds the environment, so it takes a while. When it finishes, a banner prints the bench name, the port, the default site and the most useful commands.

## 3. Start the services

```bash
devenv up
```

`devenv up` runs the whole stack through process-compose: MariaDB, Redis, nginx, the Frappe runtime, the asset watcher and Mailpit. Leave it running in this terminal. Everything that can be is on a unix socket, and the ports that remain are derived from the bench name, so you can run several benches at once. [The development shell](development/README.md) lists every process and port.

## 4. Create the site

In a second terminal, in the same directory:

```bash
provision-site
```

`provision-site` creates the site named by `FRAPPE_SITE` and installs every app listed in `sites/apps.txt`. It sets the Administrator password to `admin` unless you pass another one as the first argument:

```bash
provision-site '<ADMIN_PASSWORD>'
```

If the script prompts for a MariaDB root password, leave it blank and press Enter, because the development MariaDB root has none.

> [!WARNING]
> `provision-site` runs `bench new-site` with `--force`, which drops an existing database for that site. Do not run it again to pick up an app you added later. When `siteName` is set, `devenv up` installs missing apps for you. See [Apps in a bench](development/apps.md#installed-app-drift).

## 5. Open the site

Open `http://localhost:<PORT>`. The port is 8000 plus an offset hashed from the bench name, and the shell banner prints it. Log in as `Administrator` with the password you chose.

Anything the site tries to send by email lands in Mailpit instead of leaving your machine. Its web address is in the banner too.

## Where next

- [Everyday commands](development/commands.md) covers `bench update`, `bench get-app` and the other commands the shell redirects.
- [Write the flake by hand](scaffolding/write-the-flake.md) shows what the scaffolder wrote and the options you will touch most.
- [Development guard rails](development/guard-rails.md) explains what keeps a restored production database from reaching the outside world.
- [Restore a production backup](development/restore-from-production.md) shows how to clone production into the shell.
- When you are ready to deploy, read [From development to production](production/README.md).
