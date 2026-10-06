---
title: Development guard rails
description: The devguard options that stop a bench restored from production from mailing customers, deleting production files or pushing backups, and the limits of that protection.
order: 2
tags: [devguard, safety, mail, backups]
updated: 2026-10-06
---

A bench restored from a production backup carries working production credentials in its database and `site_config.json`. Left alone, `devenv up` will mail real customers within minutes, delete production files out of an object store within the hour, and, depending on what is configured, capture real payments, push its dev-mutated database over the production backup rotation and delete real calendar events.

`frappe-nix.devguard` closes those routes. Nothing is installed into any site and no config is edited. Each guard is independently toggleable, and `devguard.enable = false` turns them all off. The guards are on by default.

## The guards

| Guard          | What it stops                                                                          | How                                                                                                                                                                                                                                                |
| -------------- | -------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `mail`         | Any mail leaving the machine                                                           | Redirects SMTP to Mailpit and refuses IMAP and POP3.                                                                                                                                                                                               |
| `backups`      | Dropbox, S3, Google Drive and Frappe Cloud backup upload                               | No-ops the scheduler entries, blocks the upload funnels and throws on the desk buttons.                                                                                                                                                            |
| `objectstore`  | Deleting from, or overwriting in, the production bucket                                | Drops S3 deletes and refuses overwrites at botocore. Local mode, the default, also forces `cloud_storage`'s `use_local`. Push mode lets new attachments upload.                                                                                    |
| `integrations` | Outbound HTTP via `frappe.integrations.utils.make_request`                             | Refuses non-loopback hosts unless listed in `allowHosts`.                                                                                                                                                                                          |
| `google`       | Calendar, Contacts and Drive access, whose sync writes back and can delete real events | Blocks GoogleOAuth's service-object and token-refresh calls.                                                                                                                                                                                       |
| `webhooks`     | Webhook rows firing at production endpoints                                            | No-ops `enqueue_webhook`.                                                                                                                                                                                                                          |
| `plaid`        | Bank sync against the production Plaid item                                            | Blocks `PlaidConnector` and no-ops the hourly job.                                                                                                                                                                                                 |
| `scheduler`    | Third-party backup jobs and Server Script scheduler events                             | Skips them in `ScheduledJobType.execute`. The denylist matches exact dotted paths: the seven Dropbox, S3 and Google Drive backup jobs in Frappe core up to version 15, and the same seven in the standalone `offsite_backups` app from version 16. |

Local backups are untouched by all of this. `bench backup`, `bench restore`, `trim-database`, `drop-site` and the desk Backups page keep working. Only egress is blocked.

The mail guard sends everything to Mailpit. Open its web UI, at the address printed in the shell banner, to see what your site tried to send. Every option is in the [reference](../reference/dev-shell-options.md#development-guard-rails).

## Incoming mail

Incoming mail is blocked by default. A dev bench polling production mailboxes every 10 minutes marks real messages as seen and fires auto-replies. Set `devguard.mail.pop3.enable = true` to serve incoming mail from Mailpit's POP3 listener instead. Frappe issues `DELE` after fetching and Mailpit honors it, so pulled messages disappear from the Mailpit UI.

## Turning guards off

Per command, without a rebuild:

```bash
FRAPPE_DEVGUARD_DISABLE=backups,google bench console   # named guards, one command
FRAPPE_DEVGUARD_ENABLED=0 bench console                # all of them
```

Values the Nix configuration bakes in are likewise overridable at runtime, without a rebuild. Each setting has an environment variable named `FRAPPE_DEVGUARD_<GUARD>_<SETTING>`, such as `FRAPPE_DEVGUARD_MAIL_HOST`, `FRAPPE_DEVGUARD_MAIL_PORT` and `FRAPPE_DEVGUARD_INTEGRATIONS_ALLOW_HOSTS`.

Settings resolve in this order, highest first: the `FRAPPE_DEVGUARD_*` environment variables, then a runtime file that `devenv up` writes to `$DEVENV_RUNTIME/devguard-runtime.json` (Mailpit's ports go through devenv's allocator, which walks forward if a port is taken, so the port Nix baked in can differ from the one Mailpit bound), then the values Nix baked in at build time, then the built-in defaults. Because the baked values are there, the guards still hold when the devenv environment is absent, such as an editor terminal or a stray `sudo -u`. Settings are read when a guard runs, which is why a guard can be turned off for a single command.

## Pushing attachments to production

Documents usually reach production as fixtures, and their attachments through the object store. `devguard.objectstore.mode = "push"` supports that. `cloud_storage` stays on the bucket named in the dev site's `cloud_storage_settings`, which `bench restore` never carries over, so you add it. New attachments then upload there, and files inherited from the dump resolve instead of returning 404.

The bucket is still additive-only. Every S3 call in the bench passes through botocore's `BaseClient._make_api_call`, and there the guard:

- lets reads through;
- lets a new object be written only if its key is free, checked with `HeadObject` and sent with `If-None-Match: *` so the store refuses a racing write too;
- drops `DeleteObject` and `DeleteObjects` without touching the network;
- refuses everything else, including bucket policy, lifecycle rules, ACLs and tagging.

Presigned URLs are issued for reads only. So deleting a File in dev removes its row and never production's object. An attachment whose key production already holds, the same file name on the same document, is refused rather than versioned. A file pushed by mistake has to be removed from production.

`FRAPPE_DEVGUARD_OBJECTSTORE_MODE=push` does the same for a single command without a rebuild.

**Backblaze B2** does not implement `If-None-Match` and answers any write carrying it with `501 Not Implemented`. botocore misreads that reply, whose status line has no reason phrase, as "Connection was closed before we received a valid response". `cloud_storage` only logs the failure and keeps the File row, so every upload looks like it worked while nothing reaches the bucket. Set `devguard.objectstore.conditionalWrites = false` for a B2 bucket. The header is left out, `HeadObject` is the only check, and a write whose key it cannot check is refused rather than sent unguarded. `FRAPPE_DEVGUARD_OBJECTSTORE_CONDITIONAL_WRITES=false` does the same for a single command.

## How it works

Frappe offers no config-only way to do this. `find_default_outgoing` consults the database before falling back to `frappe.conf`, and the backup integrations are gated by doctype rows that a production dump restores in the enabled state. The interception therefore lives below the app layer, in [`lib/devguard/frappe_devguard`](../../lib/devguard/frappe_devguard), grafted into the development virtualenv by a `.pth` file that Python executes at interpreter startup. It applies to `bench serve`, `worker`, `schedule`, `console` and any bare `./env/bin/python`.

It is deliberately **not** on `PYTHONPATH`. `apps/*` reach `sys.path` through the editable `.pth` files in the virtualenv, so an interpreter started outside the devenv environment would still import Frappe and still reach production. And it is development-only by construction: `prodPythonEnv`, the NixOS module and the containers never see it.

Each patch is checked as it is applied. If Frappe's internals move, the import fails loudly rather than leaving a silently inert guard behind.

### frappe_unixsock

[`lib/unixsock/frappe_unixsock`](../../lib/unixsock/frappe_unixsock) uses the same `.pth` mechanism but is not a guard rail. It ships to **both** virtualenvs, and therefore into `builtBench`, the containers and `services.frappe`. It carries no policy. Its whole job is to make Frappe honor a unix socket in the two places it only half does:

| Patch                                                     | Where it bites                                                                                                                                                  |
| --------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `frappe.app.serve` binds `unix://$FRAPPE_WEB_SOCKET`      | `bench serve` hardcodes `run_simple("0.0.0.0", int(port))`, so there is no other way off TCP. Inert in production, which runs `gunicorn --bind unix:` natively. |
| `frappe.connect_replica` uses `$FRAPPE_REPLICA_DB_SOCKET` | It hardcodes `socket=None`, twenty lines below the `connect()` that honors `db_socket`. Production-only, and only when a replica is configured.                 |

Every patch is gated on its socket actually being set, so a bench with no sockets installs nothing and cannot be broken by a Frappe upgrade moving a target. A bench that is on sockets fails loudly instead, because silently falling back to TCP would mean connecting to another project's service. `FRAPPE_UNIXSOCK_ENABLED=0` disables it for a single command.

Shipping it to production does not weaken devguard's development-only guarantee: the two packages are separate and share no code. Guarding against reaching production is meaningless in production. Correcting a socket transport is not.

## What this is not

> [!WARNING]
> Treat the guard rails as a large reduction in blast radius, not an airgap.

Only the `mail` guard offers **transport-level** containment. It patches `smtplib`, `imaplib` and `poplib`, which know nothing about Frappe and so hold across upgrades, third-party apps and `override_doctype_class` controllers.

Every other guard patches Frappe and app APIs, and is therefore one refactor or one unknown app away from being bypassed. The `scheduler` denylist covers exactly the dotted paths in it. Egress from a document event in an app nobody has looked at is not covered.

Two related notes for a restored bench. Frappe's telemetry is inert here only because `developer_mode: 1` is set, so re-check it if you ever clear that flag. And `check_for_update` and `fetch_changelog_feed` still reach github.com and frappe.io, which is harmless and deliberately left alone.

Restoring production data also carries production's encryption key, which can decrypt every stored credential in the dump. `bench restore` refuses to write that key into a bench with `devguard.enable = false`. See [Restore a production backup](restore-from-production.md#the-encryption-key).
