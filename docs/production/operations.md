---
title: Operate a deployed site
description: Create or restore a site on a services.frappe host, deploy upgrades with snapshot-protected migrations, tune the runtime and find where things live.
order: 4
tags: [operations, migrations, backups, runtime-tuning]
updated: 2026-10-06
---

This page covers the operational side of a [`services.frappe`](nixos-service.md) host: getting a site installed, upgrading it safely, tuning the runtime and knowing where the state is.

## Create or restore the site

The module deploys code and configuration. It does not install a site. On the first deploy the database exists but has no tables, so the migrate unit logs that the site is not installed and exits cleanly. Other sites on the host are unaffected.

The module installs a `bench` wrapper on the system `PATH`. With exactly one enabled site it selects that site automatically. With several, set `FRAPPE_SITE` yourself:

```bash
sudo -u frappe env FRAPPE_SITE=site1.example.com bench migrate
```

Run the wrapper as the service user, so the files it creates stay owned by `frappe`. For `restore`, `migrate`, `console` and `clear-cache` it passes `--site` for you.

To load an existing database, restore an SQL backup:

```bash
sudo -u frappe env FRAPPE_SITE=site1.example.com bench restore <SQL_FILE_PATH>
```

To create a brand-new site, run `bench new-site` on the host, as the module's own migrate message instructs. Follow the Frappe bench documentation for the flags that fit your layout. After either route, run `nixos-rebuild switch` again, or restart the migrate unit:

```bash
sudo systemctl restart frappe-migrate-site1.example.com.service
```

The migrate unit records a build marker only after a successful migration, so it runs again even if the build has not changed.

## Safe migrations on deploy

To upgrade apps, update the bench input and rebuild:

```bash
nix flake update bench
sudo nixos-rebuild switch --flake .#<HOST_NAME>
```

Whenever a new build is deployed, the `frappe-migrate-<SITE>` oneshot runs `bench migrate` for each site. It re-runs only when the build actually changes. The last migrated build's store path is recorded in `<siteDir>/.frappe-migrate-build`, and re-migration is skipped when it is unchanged.

A site whose database holds **no tables** has never been installed. It is a fresh deploy waiting on `bench new-site`, or a host that lost its local database state while the site directory, on shared storage, survived. `bench migrate` cannot run there. It dies on its first query, `Table '<db>.tabDefaultValue' doesn't exist`. Installing or restoring a site is an operational step, so the unit says what is missing and exits 0 rather than failing activation, because one uninstalled site must not block every other site's deploy, on every deploy. No build marker is recorded, so the first deploy after the restore migrates even if the build has not changed.

Frappe migrations perform DDL (`CREATE` and `ALTER TABLE`), which auto-commits in MariaDB and cannot be rolled back in a transaction. The unit therefore wraps the migration in a physical snapshot instead:

1. **Snapshot.** `mysqldump --single-transaction` of the site database to `<siteDir>/snapshots/premigrate-<SITE>-<TIMESTAMP>.sql.gz`, readable only by its owner (mode 0600). If the snapshot cannot be taken, the migration is aborted. It never migrates without a safety net.
2. **Migrate.** `bench --site <SITE> migrate`, with the site in maintenance mode.
3. **On success.** Clear maintenance mode, record the build and prune old snapshots.
4. **On failure.** Restore the snapshot by dropping all current tables and re-importing the dump, **leave the site in maintenance mode**, log `MIGRATION FAILED` to the journal, and exit non-zero, so the unit shows `failed`. The database is returned to its pre-migrate state.

With `migrate.offline.enable`, step 2 is preceded by an online alteration of the large tables the migrate is about to alter, under the same snapshot and maintenance mode. A failure there takes the failure path in step 4. See [Migrate large tables online](../development/online-migrations.md).

The unit runs as the `frappe` user with the site's own database credentials, so it needs no database root and works for both locally created and externally managed databases.

> [!NOTE]
> Writes that land between the snapshot and the moment maintenance mode turns on are not in the snapshot. The site's runtime unit is ordered after the migrate unit but does not require it, so a failed migration does not stop the site from starting. Maintenance mode is what keeps visitors off the old schema.

If a migration fails, read the error, fix the problem and deploy again. Recover with a fixed forward deploy, or return to the previous system generation with `nixos-rebuild switch --rollback`.

```bash
journalctl -u frappe-migrate-site1.example.com -p err
```

### Tune or disable migration

| Option                         | Default  | Effect                                                                           |
| ------------------------------ | -------- | -------------------------------------------------------------------------------- |
| `migrate.enable`               | `true`   | Run `bench migrate` on each new build.                                           |
| `migrate.snapshot`             | `true`   | Take the pre-migrate dump.                                                       |
| `migrate.rollbackOnFailure`    | `true`   | Restore the dump on failure. Needs `snapshot`.                                   |
| `migrate.maintenanceMode`      | `true`   | Use maintenance mode around the migration. It is left on if the migration fails. |
| `migrate.snapshotRetention`    | `3`      | Snapshots kept per site under `<siteDir>/snapshots`.                             |
| `migrate.offline.enable`       | `false`  | Alter large tables online before `bench migrate`. Needs the `TRIGGER` privilege. |
| `migrate.offline.rowThreshold` | `100000` | Rows at which a table is altered online.                                         |

> [!TIP]
> A snapshot of a very large database on every deploy is expensive in time and disk. Weigh that against losing the safety net before you set `migrate.snapshot = false`. If you set `migrate.enable = false`, run `bench migrate` yourself after each deploy.

## Tune the runtime

The unified runtime exposes its limits as options. The defaults are:

| Option                         | Default | Meaning                                                                                          |
| ------------------------------ | ------- | ------------------------------------------------------------------------------------------------ |
| `runtime.jobThreads`           | `4`     | Concurrent background jobs inside the runtime process.                                           |
| `runtime.webThreads`           | `0`     | Concurrent web requests. `0` keeps the runtime's own default. Size the database pool against it. |
| `runtime.restartAfterRequests` | `5000`  | Graceful restart after this many web requests. `0` disables it.                                  |
| `runtime.restartAfterJobs`     | `500`   | Graceful restart after this many background jobs. `0` disables it.                               |
| `runtime.restartIdleSeconds`   | `300`   | Graceful restart after this much idle time. `0` disables it.                                     |
| `runtime.requestDrainSeconds`  | `60`    | How long a stop waits for in-flight web requests.                                                |
| `runtime.jobDrainSeconds`      | `600`   | How long a stop waits for a job in progress.                                                     |
| `runtime.extraArgs`            | `[ ]`   | Extra arguments appended to the `frappe-runtime` command line.                                   |

The unit's stop timeout is derived from the two drain values plus 30 seconds, 690 seconds with the defaults, so systemd outlasts the drain and does not `SIGKILL` partway through. A restart during a deploy can therefore wait that long for a running job.

To go back to separate units, set `runtime.enable = false`. You then get `frappe-web-<SITE>` (gunicorn), `frappe-scheduler-<SITE>`, `frappe-socketio-<SITE>` and one `frappe-worker-<QUEUE>-<SITE>` per queue. In that mode `web.workers` (default `4`) sets the number of gunicorn workers, and `workers` chooses the queues. With the runtime, `workers` is passed as `--queue`.

## Where things live

| Path                               | Contents                                                                       |
| ---------------------------------- | ------------------------------------------------------------------------------ |
| `/var/lib/frappe/<SITE>/sites`     | The site's `sites` directory, including `site_config.json` and uploaded files. |
| `/var/lib/frappe/<SITE>/bench`     | The runtime bench tree, linked to the store package.                           |
| `/var/lib/frappe/<SITE>/snapshots` | Pre-migrate database dumps.                                                    |

That is the default `siteDir`. Back up the site directory and the database. The service `PATH` already includes `git`, `gzip`, `tar`, `bash` and the MariaDB client tools that Frappe's own backup code needs. Add anything else your custom apps call through `extraPath`, such as `pkgs.gnupg` if you enable backup encryption.

For the journal, see [Logging](logging.md).
