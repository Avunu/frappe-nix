---
title: Migrate large tables online
description: Alter large tables with pt-online-schema-change ahead of bench migrate, so an ALTER cannot hold a metadata lock for the length of a copy.
order: 10
tags: [migrate, mariadb, pt-online-schema-change, locks]
updated: 2026-10-06
---

`bench migrate` alters a table with a plain `ALTER TABLE`. On a table of millions of rows that copy runs for minutes. It takes a metadata lock on the table where it starts and where it ends, and every other connection that touches the table queues behind it. On a busy site that is a migrate that stalls the site and then fails with `Lock wait timeout exceeded`, its own or somebody else's.

With `offlineMigrate.enable = true`, the migrate is preceded by `lib/offline-migrate.py`. It works out which tables the migrate is about to alter and, for each one of at least `offlineMigrate.rowThreshold` rows, applies the change with [`pt-online-schema-change`](https://docs.percona.com/percona-toolkit/pt-online-schema-change.html). A shadow copy of the table is altered and filled in chunks while triggers keep it current, then swapped in with one rename. Nothing waits. When `bench migrate` runs next, those columns exist already and its own sync finds nothing to alter on them.

```nix
perSystem.frappe-nix.offlineMigrate = {
  enable = true;
  rowThreshold = 100000; # the default
};
```

It is off by default. It adds percona-toolkit and its Perl closure to the shell, and it reads every DocType's JSON once more before each migrate.

## Where it runs

The step sits in front of `bench migrate`, `bench update` and `bench restore` alike, because all three migrate through `bench-migrate`. It stops the migrate if a table fails, rather than letting the migrate run on a half-applied plan.

| To do this                               | Run                                                                                    |
| ---------------------------------------- | -------------------------------------------------------------------------------------- |
| See what it would do                     | `bench-offline-migrate --plan`. Changes nothing, and works with the option off.        |
| Have pt-online-schema-change rehearse it | `bench-offline-migrate --dry-run`. Creates and drops the shadow table, copies no rows. |
| Skip it for one migrate                  | `FRAPPE_OFFLINE_MIGRATE=0 bench migrate`                                               |
| Change the threshold for one command     | `FRAPPE_OFFLINE_MIGRATE_ROW_THRESHOLD=<ROWS>` or `--threshold <ROWS>`. The flag wins.  |
| Pass options to pt-online-schema-change  | `bench-offline-migrate -- <OPTIONS>`                                                   |

On NixOS, `services.frappe.migrate.offline.enable` puts the same step inside the `frappe-migrate-<SITE>` unit, after the snapshot and the switch to maintenance mode and in front of `bench migrate`. A failure there is a failed migrate: the snapshot is restored and the site stays in maintenance mode. See [Operate a deployed site](../production/operations.md#safe-migrations-on-deploy).

## What it plans from

It plans from what `bench migrate` itself derives a table's schema from:

- the DocType JSON an installed app ships, judged changed by the same hash test `bench migrate` applies;
- the Custom Field JSON an app syncs on migrate.

Frappe's own `MariaDBTable` produces the ALTER, with its DDL captured instead of run, so the change is the one the migrate would have made.

## What it deliberately does not do

- **It will not write a row.** `MariaDBTable.alter()` backfills NULLs with a real `UPDATE` before a change to NOT NULL. Planning refuses every statement but a read, so such a table is reported as left to `bench migrate`. The backfill is the migrate's to run, and pt-online-schema-change would not give those rows Frappe's default.
- **It does not run patches, and it runs before they do.** A `pre_model_sync` patch that renames a column in a table the step has already widened, with `rename_field` onto a name the step added, fails on the duplicate column. Run that one migrate with `FRAPPE_OFFLINE_MIGRATE=0`.
- **It does not see Property Setters** from `custom/*.json`. A column whose length or type one changes is still altered by `bench migrate`, in place.
- **It assumes one node.** A site's database user owns its database and nothing else, so it may not run `SHOW SLAVE STATUS`, which pt-online-schema-change would otherwise issue to look for replicas and for a row-based source. The defaults are `--recursion-method=none` and `--force`. A bench that replicates passes `-- --recursion-method=processlist`, or `hosts`.

## What it needs

pt-online-schema-change needs the `TRIGGER` privilege on the site's database, which a locally created site user has, and one primary key per table, which every Frappe table does.

Its progress streams as it runs. Credentials reach it in a `0600` option file that is deleted afterwards, never on its command line.

> [!NOTE]
> nixpkgs' percona-toolkit scripts start with `#!/usr/bin/env perl` and die under a systemd unit's `PATH`. frappe-nix hands the tool a wrapper, `lib/offline-migrate.nix`, with perl beside it.

> [!TIP]
> A migrate that waits on `Waiting for table metadata lock` is not always a large table. Another connection can hold a shared lock on a small, busy table such as `tabDocField` for as long as its transaction stays open, and pt-online-schema-change needs the same lock to create its triggers and to swap the tables. If the stalled `ALTER` is on a table of a few thousand rows, look for the open transaction instead: `SELECT * FROM information_schema.INNODB_TRX` and `SHOW FULL PROCESSLIST` while it hangs.
