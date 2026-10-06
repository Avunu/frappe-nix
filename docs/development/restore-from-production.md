---
title: Restore a production backup
description: Clone production into the dev shell with bench restore, fetching the newest backup from the object store, and understand what the encryption key carries.
order: 4
tags: [restore, backups, object-store, devguard]
updated: 2026-10-06
---

With backup access declared, `bench restore` fetches a production backup from the object store and restores it into the dev site. A fresh clone of a bench needs nothing but `direnv allow`, `devenv up` and `bench restore`.

Before you can use it, declare your [secrets](secrets.md) and run `setup-backup-access`.

## Commands

```bash
bench restore                       # the newest backup
bench restore --list                # what is available
bench restore --at 20260814_000042  # a specific one
bench restore --files               # also the public files archive
bench restore ./dump.sql.gz         # an explicit file, no object store
```

With no file, `bench restore` reads the `backup-access` secret, finds the newest backup folder, downloads the database and the site-config backup, and restores them. It **creates the site first if it does not exist**. The first shell entry checks the apps out, see [Apps in a bench](apps.md#local-apps).

| Flag                     | Effect                                              |
| ------------------------ | --------------------------------------------------- |
| `--list`                 | Show the available backups and exit.                |
| `--at <YYYYMMDD_HHMMSS>` | Restore a specific backup instead of the newest.    |
| `--files`                | Also restore the public files archive.              |
| `--private-files`        | Also restore the private files archive.             |
| `--no-cache`             | Download again even if the cached copy is intact.   |
| `--no-site-config`       | Do not carry any key from production's site config. |
| `--no-migrate`           | Skip `bench migrate` afterwards.                    |
| `--no-force`             | Fail instead of replacing an existing database.     |
| `--encryption-key <KEY>` | Override the backup encryption key.                 |

Anything else is passed through to the real `bench restore`. With an explicit file, the object store is not involved and the command behaves as it always did. The file archives are routinely tens of gigabytes, so `restore.withFiles` defaults to `"none"`.

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

`bench restore` therefore refuses to write the key into a bench with `devguard.enable = false` (the `restore.requireDevguard` option, on by default). `--no-site-config` restores without it, and the site still works with its stored credentials opaque.

One consequence worth stating plainly: the backup's site-config copy is **never encrypted**, even when the database beside it is. `backup_encryption()` covers the dump and the two archives, not the config. Anyone who can read your backup bucket can read production's encryption key, so keep the bucket private.

## Options

The restore behavior is configured under `frappe-nix.restore`:

| Option                    | Default                                        | Notes                                                                       |
| ------------------------- | ---------------------------------------------- | --------------------------------------------------------------------------- |
| `restore.enable`          | `frappe-nix.secrets.backupAccess.enable`       | Let `bench restore` fetch from the object store.                            |
| `restore.prefix`          | `""`                                           | Path inside the bucket. Normally carried in the secret as `BACKUPS_PREFIX`. |
| `restore.withFiles`       | `"none"`                                       | `none`, `private` or `all`: the file archives to pull by default.           |
| `restore.carryConfigKeys` | `[ "encryption_key" "backup_encryption_key" ]` | Allowlist of keys copied from the backup's site config.                     |
| `restore.migrate`         | `true`                                         | Run `bench migrate` after restoring.                                        |
| `restore.requireDevguard` | `true`                                         | Refuse to write production's encryption key into an unguarded bench.        |

With no backup source configured, `bench restore` says so and tells you to pass a file or declare the `backup-access` secret. On a multi-tenant bench (`siteName = ""`) with `FRAPPE_SITE` unset there is no site to restore into: set `FRAPPE_SITE` in `.env`, or pass a file.

If you push attachments from the dev shell to the production bucket, see [Pushing attachments to production](guard-rails.md#pushing-attachments-to-production).
