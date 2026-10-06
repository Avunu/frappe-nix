---
title: Logging
description: Everything services.frappe logs goes to the journal with APP_SERVICE and APP_SITE fields and real syslog priorities, including nginx's JSON access log.
order: 5
tags: [logging, journald, nginx, observability]
updated: 2026-10-06
---

On a [`services.frappe`](nixos-service.md) host everything goes to the journal and nothing writes a log file. That covers Frappe (`FRAPPE_STREAM_LOGGING=1`, and a caller asking for a file is overridden), bench (`bench/logs/bench.log` is no longer written) and nginx (no `/var/log/nginx/access.log`).

The exceptions are Frappe features that write a file as their product rather than as logging, and only when used: the request monitor (`monitor` in `site_config`, `logs/monitor.json.log`) and the setup wizard's `logs/setup-wizard.log`.

A host that ships the journal to a log store filters by the fields below and by `PRIORITY`, so both are part of the module's contract. It is the same contract odoo-nix and wordpress-nix follow.

## Reading the journal

With the unified runtime, the site's unit is `frappe-<SITE>`:

```bash
journalctl -u frappe-site1.example.com -f
journalctl -u frappe-init-site1.example.com
```

Because every unit carries journald fields, you can also filter across units:

```bash
journalctl APP_SERVICE=migrate APP_SITE=site1.example.com
journalctl APP_SERVICE=runtime -p warning
journalctl SYSLOG_IDENTIFIER=nginx_access -o cat
```

## Fields

Every unit the module defines carries `LogExtraFields`, which journald attaches to everything the unit's processes log, whether through stdout, stderr or syslog.

| Unit                              | `APP_SERVICE` | `APP_SITE` | `SYSLOG_IDENTIFIER`                            |
| --------------------------------- | ------------- | ---------- | ---------------------------------------------- |
| `frappe-<SITE>` (unified runtime) | `runtime`     | the site   | `frappe-runtime`                               |
| `frappe-web-<SITE>`               | `web`         | the site   | `frappe-web`                                   |
| `frappe-worker-<QUEUE>-<SITE>`    | `worker`      | the site   | `frappe-worker-<QUEUE>`                        |
| `frappe-scheduler-<SITE>`         | `scheduler`   | the site   | `frappe-scheduler`                             |
| `frappe-socketio-<SITE>`          | `socketio`    | the site   | `frappe-socketio`                              |
| `frappe-migrate-<SITE>`           | `migrate`     | the site   | `frappe-migrate`                               |
| `frappe-init-<SITE>`              | `init`        | the site   | `frappe-init`                                  |
| `frappe-db-password-<SITE>`       | `init`        | the site   | `frappe-db-password`                           |
| `mysql`                           | `db`          | none       | MariaDB's own                                  |
| `redis-frappe`                    | `redis`       | none       | Redis's own                                    |
| `nginx`                           | `nginx`       | none       | `nginx`, and `nginx_access` for the access log |

The shared units serve every site on the host, so they carry no `APP_SITE`. Each access-log entry names its site in its JSON instead.

## Priorities

The bench's virtualenv carries `frappe_journald` ([`lib/journald`](../../lib/journald), grafted like `frappe_unixsock` into both virtualenvs). When stderr is the journal, it reformats Frappe's loggers and bench's own as `<N>{module} [{site}] {message}`. It checks `JOURNAL_STREAM` against stderr's device and inode, so a terminal, the dev shell, the OCI images and a captured subprocess keep stock output.

journald reads the `<N>` as the line's syslog priority (debug 7, info 6, warning 4, error 3, critical 2) and stamps the time itself, so the timestamp and level name are gone from the message. The site is added because Frappe's own format has none. Every line of a multi-line record carries the prefix, so a traceback stays at error from its first line to its last instead of dropping to info after the first.

`frappe-runtime` formats its root logger the same way, and the migrate unit's failure lines are at error, so `journalctl -u frappe-migrate-<SITE> -p err` finds a failed migration. `-p` filters work as you would expect on Python log lines.

## Log level

`services.frappe.logging.level` is the threshold for `frappe.logger()` and for bench's log, passed as `FRAPPE_LOG_LEVEL`. It accepts `debug`, `info`, `warning` and `error`, and defaults to `warning`. Frappe's own production default is error, which silently drops every `frappe.logger().warning()`.

An explicit `set_log_level()` in code still wins. The runtime's lifecycle lines stay at info, and gunicorn, Node, MariaDB and Redis keep their own levels. The units also set `PYTHONUNBUFFERED=1`, so `print()` output is neither held back nor lost in a crash.

## The nginx access log

`services.frappe.logging.accessLog` (default `true`) sends nginx's access log to the journal over `/dev/log`, one JSON object per request, under `SYSLOG_IDENTIFIER=nginx_access`. nginx writes each object on a single line. It is shown formatted here:

```json
{
  "time": "2026-09-29T12:00:00+00:00",
  "site": "erp.example.com",
  "method": "GET",
  "uri": "/app",
  "status": 200,
  "bytes": 5120,
  "request_time": 0.042,
  "upstream_time": "0.041",
  "remote_addr": "203.0.113.7",
  "user_agent": "…",
  "referer": "…"
}
```

`upstream_time` is a string. It is `-` for a request with no upstream, such as `/assets/`, and a comma-separated list when nginx retried one.

> [!NOTE]
> Access logging is set at the http level, so `logging.accessLog` applies to every virtual host on the machine and not per site. Setting it to `false` turns access logging off for all of them.

nginx's error log goes to syslog too (`services.nginx.logError`, overridable), so its `[error]` and `[crit]` lines keep their severity.
