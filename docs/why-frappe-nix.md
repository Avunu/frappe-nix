---
title: Why frappe-nix
description: What building a Frappe bench with Nix gives a business, what frappe-nix adds on top of Nix, and the cases where you should not use it.
order: 1
tags: [nix, reproducibility, decision]
updated: 2026-10-06
---

This page explains what Nix buys a business that runs its own Frappe or ERPNext system, what frappe-nix adds, and where it is the wrong tool. Read it before you decide to adopt frappe-nix, then follow the how-to pages.

## What goes wrong with a conventional bench

A traditional server is a pile of history: packages installed by hand, a virtualenv someone rebuilt last spring, a `yarn install` that resolved differently on Tuesday. Nobody can say exactly what is running, and nobody can rebuild it from scratch with confidence.

Nix replaces that with a build. Every input is locked, every output is a function of those inputs, and the result is the same on a laptop, in CI and on the server.

## What Nix gives a business

### Reproducible builds

In frappe-nix the Python environment comes from `uv.lock`, the JavaScript dependencies come from each app's own `yarn.lock`, and the system packages come from a pinned `flake.lock`. The same inputs produce the same result everywhere.

The production package, `builtBench`, goes one step further: it runs `bench build` at build time. Compiled assets are part of the artifact instead of something created on the server after deployment.

### Configuration you can read and review

On NixOS the server is described by files in git. A change to the database, the web server or the number of workers is a commit that someone can review, and the history answers "who changed this and why" without detective work. A site in frappe-nix is a few lines of Nix:

```nix
services.frappe = {
  enable = true;
  package = bench.packages.x86_64-linux.default;
  database.createLocally = true;
  redis.createLocally = true;
  sites."erp.example.com" = {
    enable = true;
    database.createLocally = true;
    database.passwordFile = config.age.secrets.db-password.path;
    encryptionKeyFile = config.age.secrets.encryption-key.path;
    nginx.enable = true;
  };
};
```

Secrets stay out of this file and out of the Nix store. The module reads passwords from files at start-up and writes the final `site_config.json` with restrictive permissions. For secrets that travel with a project, frappe-nix supports [age](https://github.com/FiloSottile/age)-encrypted files committed next to the code, and a `check-secrets` command that verifies the ciphertext is encrypted to the people you think it is. See [Secrets](development/secrets.md).

### Rollbacks

Every NixOS change produces a new system generation, and the old one stays on disk until you clean it up. If a deploy goes badly, you step back:

```bash
nixos-rebuild switch --rollback
```

Rollback covers code and configuration. It does not cover your data, so the module adds a database safety net around migrations. Before it runs `bench migrate` on a deploy it takes a `mysqldump` snapshot. If the migration fails, it restores the snapshot and leaves the site in maintenance mode. See [Operate a deployed site](production/operations.md#safe-migrations-on-deploy).

### No lock-in

A Nix configuration is text that describes how to build and run the system. It does not depend on a vendor's control panel. The same bench produces both a NixOS module for running directly on a NixOS host and OCI container images for hosts that run containers. If you leave the people who set it up, you take the repository with you.

## What frappe-nix adds

Nix alone gives you the building blocks. frappe-nix is the part that knows how a Frappe bench works:

- **A scaffolder that replaces `bench init`.** It creates a bench, converts an existing one in place without deleting anything, or sets up a single app's repository. See [Choose a mode](scaffolding/README.md).
- **A development shell that matches production.** One `direnv allow` and `devenv up` replace a setup wiki. See [The development shell](development/README.md).
- **Guard rails.** A bench restored from production cannot mail customers, delete files from a production bucket or push backups. See [Development guard rails](development/guard-rails.md).
- **A `bench` wrapper** that sends `bench update`, `bench get-app`, `bench restore` and friends to versions that understand git submodules, the uv workspace and the read-only Nix store. See [Everyday commands](development/commands.md).
- **One runtime process** per bench that serves the web app, realtime, background jobs and the scheduler together. See [The unified runtime](production/runtime.md).
- **A NixOS module and container images** built from the same package the developers run. See [Production](production/README.md).
- **One logging contract.** Every service logs to the journal and carries `APP_SERVICE` and `APP_SITE` fields. The sibling projects [odoo-nix](https://github.com/Avunu/odoo-nix) and [wordpress-nix](https://github.com/Avunu/wordpress-nix) follow the same contract, so a single query can span a Frappe site, an Odoo database and a WordPress site. See [Logging](production/logging.md).

## Honest trade-offs

Nix is powerful and it is not free. Weigh these before you commit.

- **There is a learning curve.** You need to read the Nix language at least well enough to follow a module, and you need flakes enabled. Error messages can be long and point at the wrong layer.
- **Lock files are a new chore.** When you move an app to a commit that adds a dependency, `uv.lock` goes stale, and the bench fails to evaluate until you re-lock. frappe-nix names the problem and provides `nix run .#relock`, but you still have to run it. See [Dependencies and locks](development/locks.md).
- **Imperative habits break.** On a read-only Nix store, upstream commands that install packages into the environment do not work. frappe-nix wraps `bench update`, `bench get-app` and `bench restore` for this reason, so their flags follow frappe-nix's scripts rather than stock bench.
- **Data is still state.** Nix reproduces code and configuration, not your database or uploads. You still need backups, and you still need to rehearse restoring them. Creating a site on a deployed host remains an operational step.
- **Platform limits apply.** The production module needs a NixOS host. If your host is not NixOS, use the [OCI images](production/images.md).

## When not to use it

Skip frappe-nix, or postpone it, in these cases:

- **Nobody will own it.** A reproducible system that no one on your side or your partner's side understands is just a different black box. If you have no developer and no support partner, a managed host is a better fit.
- **You want to run stock bench commands unchanged.** frappe-nix redirects some of them, and the redirected commands follow its flags.
- **You need a deployment target the project does not cover.** frappe-nix produces a NixOS module and OCI images. Other targets mean writing your own glue.
- **You are in a rush.** Moving an existing bench onto frappe-nix is a reconcile step, not a rewrite, but it still takes planning and a test restore.

> [!TIP]
> You do not have to adopt everything at once. The development shell alone is a good first step: it gives every developer the same environment, and nothing about it requires you to change how production runs.

## Further reading

- [Getting started](getting-started.md) takes you from nothing to a running bench.
- The Avunu knowledge base has a longer essay on [Nix for business systems](https://avunu.net/kb/nixos/why-nix-for-business-systems/), which also covers odoo-nix and wordpress-nix.
