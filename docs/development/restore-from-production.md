---
title: Restore a production backup
description: Clone production into the dev shell with bench restore, fetching the newest backup from the object store, and understand what the encryption key carries.
order: 4
tags: [restore, backups, object-store, devguard]
updated: 2026-10-16
---

With backup access declared, `bench restore` fetches a production backup from the object store and restores it into the dev site. A fresh clone of a bench needs nothing but `direnv allow`, `devenv up` and `bench restore`.

Before you can use it, declare your [secrets](secrets.md) and run `setup-backup-access`.

## Commands

```bash
bench restore                       # the newest backup
bench restore --list                # what is available
bench restore --at 2026-08-14       # the newest of that day
bench restore --at 20260814_000042  # a specific one
bench restore --files               # also the public files archive
bench restore ./dump.sql.gz         # an explicit file, no object store
```

With no file, `bench restore` reads the `backup-access` secret, finds the newest backup folder, downloads the database and the site-config backup, restores them, and neutralizes the result (see [Neutralizing](#neutralizing)). It **creates the site first if it does not exist**. The first shell entry checks the apps out, see [Apps in a bench](apps.md#local-apps).

| Flag                     | Effect                                              |
| ------------------------ | --------------------------------------------------- |
| `--list`                 | Show the available backups and exit.                |
| `--at <WHEN>`            | Restore the newest backup at or within `<WHEN>` instead of the newest of all: an ISO 8601 date or time (`2026-08-14`, `2026-08-14T00:00:42`), a backup's own name (`20260814_000042`), or any leading part of either (`2026-08` is the newest of August). Times are the backup's own clock. |
| `--files`                | Also restore the public files archive.              |
| `--private-files`        | Also restore the private files archive.             |
| `--no-cache`             | Download again even if the cached copy is intact.   |
| `--no-site-config`       | Do not carry any key from production's site config. |
| `--no-migrate`           | Skip `bench migrate` afterwards.                    |
| `--no-force`             | Fail instead of replacing an existing database.     |
| `--neutralize`           | Neutralize the restored database (the default for a fetched backup). |
| `--no-neutralize`        | Leave the restored database as production had it.   |
| `--encryption-key <KEY>` | Override the backup encryption key.                 |

Anything else, and everything after `--`, is passed through to the real `bench restore`. With an explicit file, the object store is not involved and the database is restored as it is, unless you pass `--neutralize`. The file archives are routinely tens of gigabytes, so `restore.files` defaults to `"none"`.

> [!WARNING]
> Without `--no-force`, restoring replaces the database of the dev site. That is what you want in a dev shell, and it is why the guard rails matter. See [Development guard rails](guard-rails.md).

## How it finds the backup

`bench restore` reads Frappe's own layout. `S3 Backup Settings` writes one folder per backup named `YYYYMMDD_HHMMSS`, holding the database, a verbatim copy of production's `site_config.json`, and optionally the two file archives.

Downloads are cached under `$DEVENV_STATE`, keyed by folder. The name is a timestamp, so it is also the version, and a re-run of the same restore downloads nothing.

The credentials are decrypted at the moment they are used and not at shell entry. agenix-shell re-runs `rage` every time its script is sourced, and `enterShell` runs on every direnv reload, so loading them there would prompt for a passphrase on every file save. It also exports the plaintext itself, not just a path, which would put credentials in the environment of every process in the session, `devenv up`'s children included.

## The encryption key

The backup folder contains production's `site_config.json` verbatim. That is how the `restore.carryConfigKeys` option gets `encryption_key` and `backup_encryption_key`:

- Without the first, every stored password and API secret in the dump decrypts to nothing.
- The second opens the next encrypted backup.

Both are written into the dev site's `site_config.json` at mode 0600.

It is an allowlist rather than a denylist, because a denylist loses to the next app that invents `foo_api_secret`. Everything else production had, such as `host_name`, `db_*`, `mail_*`, `cloud_storage_settings` and `maintenance_mode`, is left behind.

> [!CAUTION]
> This is a real capability, not a formality. A bench holding that key can decrypt every stored production credential in the dump: mail passwords, payment secrets and API tokens. It is what makes a restore a clone rather than a shell, and it is precisely what the [guard rails](guard-rails.md) exist to survive.

`bench restore` therefore refuses to restore into a bench with `devguard.enable = false` at all, from a fetched backup or an explicit file (the `restore.requireDevguard` option, on by default). The key is not the only risk: the database alone carries production's customers, its queued mail and its scheduled jobs. `FRAPPE_NIX_RESTORE_ALLOW_UNGUARDED=1 bench restore` overrides it for one restore. `--no-site-config` restores without the key, and the site still works with its stored credentials opaque.

## Neutralizing

A restore from the object store is neutralized as a second line of defence below the guard rails, which refuse the connections themselves:

- the scheduler is switched off in the restored database (System Settings, as `bench disable-scheduler` does), so production's jobs do not run here;
- every Email Account in it has sending and receiving switched off, so production's queued mail has nothing to go out through.

`bench enable-scheduler` turns the scheduler back on when you want to work on a job. `--no-neutralize`, or `restore.neutralize = false`, leaves the database as production had it.

One consequence worth stating plainly: the backup's site-config copy is **never encrypted**, even when the database beside it is. `backup_encryption()` covers the dump and the two archives, not the config. Anyone who can read your backup bucket can read production's encryption key, so keep the bucket private.

## Options

The restore behavior is configured under `frappe-nix.restore`:

| Option                    | Default                                        | Notes                                                                       |
| ------------------------- | ---------------------------------------------- | --------------------------------------------------------------------------- |
| `restore.enable`          | `frappe-nix.secrets.backupAccess.enable`       | Let `bench restore` fetch from the object store.                            |
| `restore.prefix`          | `""`                                           | Path inside the bucket. Normally carried in the secret as `BACKUPS_PREFIX`. |
| `restore.files`           | `"none"`                                       | `none`, `private` or `all` (or `true`/`false`): the file archives to pull by default. Formerly `restore.withFiles`. |
| `restore.carryConfigKeys` | `[ "encryption_key" "backup_encryption_key" ]` | Allowlist of keys copied from the backup's site config.                     |
| `restore.migrate`         | `true`                                         | Run `bench migrate` after restoring.                                        |
| `restore.neutralize`      | `true`                                         | Pause the scheduler and switch mail off in a database fetched from the object store. |
| `restore.requireDevguard` | `true`                                         | Refuse to restore into an unguarded bench.                                  |

With no backup source configured, `bench restore` says so and tells you to pass a file or declare the `backup-access` secret. On a multi-tenant bench (`defaultSite = ""`) with `FRAPPE_SITE` unset there is no site to restore into: set `FRAPPE_SITE` in `.env`, or pass a file.

If you push attachments from the dev shell to the production bucket, see [Pushing attachments to production](guard-rails.md#pushing-attachments-to-production).
