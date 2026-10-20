---
title: Development guard rails
description: The devguard options that stop a bench restored from production from mailing customers, calling payment gateways and other APIs, deleting production files or pushing backups, and the limits of that protection.
order: 2
tags: [devguard, safety, egress, mail, backups]
updated: 2026-10-19
---

A bench restored from a production backup carries working production credentials in its database and `site_config.json`. Left alone, `devenv up` will mail real customers within minutes, delete production files out of an object store within the hour, and, depending on what is configured, capture real payments, create real direct-debit mandates and subscriptions, push its dev-mutated database over the production backup rotation and delete real calendar events.

`frappe-nix.devguard` closes those routes. Nothing is installed into any site and no config is edited. Each guard is independently toggleable, and `devguard.enable = false` turns them all off. The guards are on by default, and the shell banner says so.

## The guards

| Guard          | What it stops                                                                          | How                                                                                                                                                                                                                                                |
| -------------- | -------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `egress`       | Any connection off the machine: payment SDKs, bank APIs, unguarded integrations        | Refuses `socket.connect` to a non-loopback address unless it is on `allowHosts`. Unix sockets, loopback and the mail catcher always pass.                                                                                                          |
| `mail`         | Any mail leaving the machine                                                           | Redirects SMTP to Mailpit and refuses IMAP and POP3.                                                                                                                                                                                               |
| `backups`      | Dropbox, S3, Google Drive and Frappe Cloud backup upload                               | No-ops the scheduler entries, blocks the upload funnels and throws on the desk buttons.                                                                                                                                                            |
| `objectstore`  | Deleting from, or overwriting in, the production bucket                                | Drops S3 deletes and refuses overwrites at botocore. Local mode, the default, also forces `cloud_storage`'s `use_local`. Push mode lets new attachments upload.                                                                                    |
| `integrations` | Outbound HTTP via `frappe.integrations.utils.make_request`                             | Refuses hosts on neither allowlist, naming the URL it refused.                                                                                                                                                                                     |
| `google`       | Calendar, Contacts and Drive access, whose sync writes back and can delete real events | Blocks GoogleOAuth's service-object and token-refresh calls.                                                                                                                                                                                       |
| `webhooks`     | Webhook rows firing at production endpoints                                            | No-ops `enqueue_webhook`.                                                                                                                                                                                                                          |
| `plaid`        | Bank sync against the production Plaid item                                            | Blocks `PlaidConnector` and no-ops the hourly job.                                                                                                                                                                                                 |
| `jobs`         | Third-party backup jobs and Server Script scheduler events                             | Skips them in `ScheduledJobType.execute`. The denylist matches exact dotted paths: the seven Dropbox, S3 and Google Drive backup jobs in Frappe core up to version 15, and the same seven in the standalone `offsite_backups` app from version 16. |

Local backups are untouched by all of this. `bench backup`, `bench restore`, `trim-database`, `drop-site` and the desk Backups page keep working. Only the uploads are blocked.

The mail guard sends everything to Mailpit. Open its web UI, at the address printed in the shell banner, to see what your site tried to send. Every option is in the [reference](../reference/dev-shell-options.md#development-guard-rails).

## Incoming mail

Incoming mail is blocked by default. A dev bench polling production mailboxes every 10 minutes marks real messages as seen and fires auto-replies. Set `mailpit.pop3.enable = true` to serve incoming mail from Mailpit's POP3 listener instead. Frappe issues `DELE` after fetching and Mailpit honors it, so pulled messages disappear from the Mailpit UI.

## Outbound connections

The `egress` guard is the catch-all behind the others, which each know one integration. Every Python client, whether requests, httpx, aiohttp, urllib, botocore or smtplib, ends in `socket.socket.connect`, and the guard refuses any connection there that is not to this machine. A payment SDK such as `gocardless_pro` therefore cannot set up real mandates or subscriptions, and neither can the next integration nobody has written a guard for. The refusal happens before a packet leaves and is an `OSError`, so a client that already handles "the service is unreachable" fails the way it would offline. The first refusal in a process says how to let a destination through, and each one names the host the address was resolved from:

```text
WARNING frappe_devguard[egress] ACTIVE -- outbound connections from this process are refused (loopback only). Allow a destination with devguard.egress.allowHosts, …
WARNING frappe_devguard[egress] blocked connection to 104.16.246.239:443 (api-sandbox.gocardless.com)
```

Unix sockets, loopback and the mail catcher always pass, so MariaDB, Redis, Socket.IO, Mailpit and the bench calling its own web server are unaffected.

To let a destination through, list it:

```nix
frappe-nix.devguard.egress.allowHosts = [
  "api-sandbox.gocardless.com" # a hostname
  "*.example.com"              # a domain and its subdomains
  "10.0.0.0/8"                 # an address or a CIDR
];
```

For a single command, `FRAPPE_DEVGUARD_EGRESS_ALLOW_HOSTS=api-sandbox.gocardless.com bench console` replaces the list. `integrations.allowHosts` is the same list: an entry in either one opens the host to both guards.

A hostname is matched against the name a connection was resolved from, because clients resolve a name and then connect to the address. The guard wraps `getaddrinfo` to remember which names produced which addresses. Two consequences follow. An address that an allowlisted name shares with another host, as on a CDN, passes for both. And a client that resolves some other way, such as aiodns or gevent's resolver, is refused unless its address or CIDR is listed.

Only sandbox credentials belong behind an allowlisted sandbox host. A production key sent to a sandbox endpoint is refused there, but a production endpoint on the list accepts it.

In push mode, the S3 calls the `objectstore` guard admits get through as well; see [Pushing attachments to production](#pushing-attachments-to-production).

## Turning guards off

Per command, without a rebuild:

```bash
FRAPPE_DEVGUARD_DISABLE=backups,google bench console   # named guards, one command
FRAPPE_DEVGUARD_DISABLE=egress bench console           # outbound connections, one command
FRAPPE_DEVGUARD_ENABLED=0 bench console                # all of them
```

For an app's tests only, `[tool.frappe-nix.tests] devguard-disable = ["mail"]` in its `pyproject.toml` turns named guards off for the processes `frappe-test` runs the tests in, and leaves the dev shell and `devenv up` guarded. That is the setting for an app whose own feature is what a guard intercepts, such as a mail transport, rather than `devguard.mail.enable = false`, which unguards the whole shell. See [Development guards in a test run](../app-standards/testing.md#development-guards-in-a-test-run).

Values the Nix configuration bakes in are likewise overridable at runtime, without a rebuild. Each setting has an environment variable named `FRAPPE_DEVGUARD_<GUARD>_<SETTING>`, such as `FRAPPE_DEVGUARD_MAIL_HOST`, `FRAPPE_DEVGUARD_MAIL_PORT` and `FRAPPE_DEVGUARD_EGRESS_ALLOW_HOSTS`.

Settings resolve in this order, highest first: the `FRAPPE_DEVGUARD_*` environment variables, then a runtime file that `devenv up` writes to `$DEVENV_RUNTIME/devguard-runtime.json` (it records the ports Mailpit was started on, which `devenv up` leased: the preferred ones from [`ports.offset`](../reference/dev-shell-options.md#ports-and-sockets), or the next free ones when another bench holds those), then the values Nix baked in at build time, which are the preferred ports, then the built-in defaults. Because the baked values are there, the guards still hold when the devenv environment is absent, such as an editor terminal or a stray `sudo -u`. Mail from such an interpreter goes to the preferred Mailpit port, so on a bench whose Mailpit was moved it reaches whichever bench holds that port: still a catcher, never a real server. Settings are read when a guard runs, which is why a guard can be turned off for a single command.

## Pushing attachments to production

Documents usually reach production as fixtures, and their attachments through the object store. `devguard.objectstore.mode = "push"` supports that. `cloud_storage` stays on the bucket named in the dev site's `cloud_storage_settings`, which `bench restore` never carries over, so you add it. New attachments then upload there, and files inherited from the dump resolve instead of returning 404.

The bucket is still additive-only. Every S3 call in the bench passes through botocore's `BaseClient._make_api_call`, and there the guard:

- lets reads through;
- lets a new object be written only if its key is free, checked with `HeadObject` and sent with `If-None-Match: *` so the store refuses a racing write too;
- drops `DeleteObject` and `DeleteObjects` without touching the network;
- refuses everything else, including bucket policy, lifecycle rules, ACLs and tagging.

Presigned URLs are issued for reads only. So deleting a File in dev removes its row and never production's object. An attachment whose key production already holds, the same file name on the same document, is refused rather than versioned. A file pushed by mistake has to be removed from production.

Push mode is also what lets the bucket through the [egress guard](#outbound-connections). The calls the objectstore guard admits (reads, the `HeadObject` probe and new-object writes) are let through the egress guard, and the ones it refuses never reach the network. That covers the S3 calls botocore makes after these checks, from any app, and nothing else: another AWS service, or a bucket reached without botocore, is refused as usual. So while push mode is on, any app's S3 client can add objects to a bucket it holds credentials for. Turn it on for the push and off again. In local mode the bucket is not contacted at all.

`FRAPPE_DEVGUARD_OBJECTSTORE_MODE=push` does the same for a single command without a rebuild.

**Backblaze B2** does not implement `If-None-Match` and answers any write carrying it with `501 Not Implemented`. botocore misreads that reply, whose status line has no reason phrase, as "Connection was closed before we received a valid response". `cloud_storage` only logs the failure and keeps the File row, so every upload looks like it worked while nothing reaches the bucket. Set `devguard.objectstore.conditionalWrites = false` for a B2 bucket. The header is left out, `HeadObject` is the only check, and a write whose key it cannot check is refused rather than sent unguarded. `FRAPPE_DEVGUARD_OBJECTSTORE_CONDITIONAL_WRITES=false` does the same for a single command.

## How it works

Frappe offers no config-only way to do this. `find_default_outgoing` consults the database before falling back to `frappe.conf`, and the backup integrations are gated by doctype rows that a production dump restores in the enabled state. The interception therefore lives below the app layer, in the `frappe_devguard` package, grafted into the development virtualenv by a `.pth` file that Python executes at interpreter startup. It applies to `bench serve`, `worker`, `schedule`, `console` and any bare `./env/bin/python`.

The package is built from two trees. The core, which knows nothing about Frappe (the post-import hook, settings resolution, the patch helpers, and the `egress` and transport-level `mail` guards), is [python-devops-nix](https://github.com/Avunu/python-devops-nix)'s `python/devguard`, shared with odoo-nix. Frappe's side, [`lib/devguard/frappe_devguard`](../../lib/devguard/frappe_devguard), holds `_product.py` (the settings prefix, frappe-nix's defaults and denylist, and the hooks into Frappe's logger and `frappe.throw`), `_frappe.py` (the helpers that know Frappe) and the guards that patch Frappe. `lib/grafts.nix` merges them, refusing a file both trees have, and bakes the Nix options in as `_baked.json`.

It is deliberately **not** on `PYTHONPATH`. `apps/*` reach `sys.path` through the editable `.pth` files in the virtualenv, so an interpreter started outside the devenv environment would still import Frappe and still reach production. And it is development-only by construction: `prodPythonEnv`, the NixOS module and the containers never see it. The `devguard-production` check builds a bench's `prodPythonEnv` through the devenv module and fails if it carries `frappe_devguard` or a devguard `.pth` file.

The dev shell exports `FRAPPE_NIX_DEVGUARD` (`1` or `0`, from `devguard.enable`) for frappe-nix's own tools. The guards do not read it.

Each patch is checked as it is applied. If Frappe's internals move, the import fails loudly rather than leaving a silently inert guard behind.

### frappe_unixsock

[`lib/unixsock/frappe_unixsock`](../../lib/unixsock/frappe_unixsock) uses the same `.pth` mechanism but is not a guard rail. It ships to **both** virtualenvs, and therefore into `builtBench`, the containers and `services.frappe-nix`. It carries no policy. Its whole job is to make Frappe honor a unix socket in the two places it only half does:

| Patch                                                     | Where it bites                                                                                                                                                  |
| --------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `frappe.app.serve` binds `unix://$FRAPPE_NIX_WEB_SOCKET`      | `bench serve` hardcodes `run_simple("0.0.0.0", int(port))`, so there is no other way off TCP. Inert in production, which runs `gunicorn --bind unix:` natively. |
| `frappe.connect_replica` uses `$FRAPPE_NIX_REPLICA_DB_SOCKET` | It hardcodes `socket=None`, twenty lines below the `connect()` that honors `db_socket`. Production-only, and only when a replica is configured.                 |

Every patch is gated on its socket actually being set, so a bench with no sockets installs nothing and cannot be broken by a Frappe upgrade moving a target. A bench that is on sockets fails loudly instead, because silently falling back to TCP would mean connecting to another project's service. `FRAPPE_UNIXSOCK_ENABLED=0` disables it for a single command.

Shipping it to production does not weaken devguard's development-only guarantee: the two packages are separate and share no code. Guarding against reaching production is meaningless in production. Correcting a socket transport is not.

## What this is not

> [!WARNING]
> Treat the guard rails as a large reduction in blast radius, not an airgap.

Two guards are **transport-level**: `egress`, which refuses connections at `socket.socket.connect`, and `mail`, which patches `smtplib`, `imaplib` and `poplib`. They know nothing about Frappe and so hold across upgrades, third-party apps and `override_doctype_class` controllers.

Both cover the bench's Python and nothing else. Outside them are:

- subprocesses: `git`, `mc`, `curl`, Chromium, wkhtmltopdf and Node open their own connections;
- C extensions that connect without Python's socket module, such as `mysqlclient`'s libmariadb or gRPC;
- UDP sent with `sendto` rather than through a connected socket;
- the browser, whose scripts reach whatever the page tells them to.

Every other guard patches Frappe and app APIs, and is therefore one refactor or one unknown app away from being bypassed. The `jobs` denylist (`frappe_devguard[scheduler]` in the log) covers exactly the dotted paths in it. What slips past an API-level guard, such as a document event in an app nobody has looked at, still meets `egress` at the socket, only with a less readable error.

Two related notes for a restored bench. Frappe's telemetry is inert here only because `developer_mode: 1` is set, so re-check it if you ever clear that flag. And `check_for_update` and `fetch_changelog_feed` now fail as they would offline, because egress refuses github.com and frappe.io. Add those hosts to `egress.allowHosts` if you want the update notices back.

Restoring production data also carries production's encryption key, which can decrypt every stored credential in the dump. `bench restore` refuses to write that key into a bench with `devguard.enable = false`. See [Restore a production backup](restore-from-production.md#the-encryption-key).
