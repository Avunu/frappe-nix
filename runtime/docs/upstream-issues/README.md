# Upstream issue drafts

Bug reports against Frappe's `develop` branch, found by running its new unified
runner (`frappe/runner.py`, `frappe/asgi.py`, `frappe/realtime/`) in anger. Each
is fixed in this fork's `src/`; the draft is what we would file upstream, and the
record of why the fork diverges from `757f127a10` where it does.

Verified against `origin/develop` at `34224e0128` (2026-09-10). `frappe/runner.py`,
`frappe/asgi.py` and `frappe/realtime/` had not changed since `757f127a10`, the
commit this package extracts from.

| Draft | Fixed in | Severity |
| --- | --- | --- |
| [01 — runner only works from `sites/`](01-runner-requires-sites-cwd.md) | `runner.py`: `SITES_PATH` in `TrafficMiddleware.load`, `chdir` in `main()` | Fails to boot; silent misconfiguration; every job fails |
| [02 — socketio env vars dropped](02-realtime-drops-socketio-env.md) | `config.py`: `_env_or_conf` | Regression vs the Node server |
| [03 — no unix socket support](03-runner-unix-socket.md) | `runner.py`: `--uds` | Feature request |

## Two of the fork's changes are *not* upstream bugs

Checking each claim against current `develop` before writing these turned up two
that do not belong in a bug report. Recording them so nobody re-files them:

**`_init_frappe_for_thread` in `runner.py` — fixed upstream already.** Job threads died on
`get_redis_conn()` with `Exception: You need to call frappe.init`, because
`frappe.local` is a `ContextVar`-backed `Local` (`frappe/utils/local.py:7`) that a
worker thread does not inherit. That raise only exists in **v16.10.9 and earlier**:

| Version | `get_redis_conn` |
| --- | --- |
| v16.10.9 | `raise Exception("You need to call frappe.init")` |
| v16.33.1 | falls back via `frappe.get_conf()` |
| develop | falls back via `frappe.get_conf()` |

`get_conf` (`frappe/config.py:158`) returns `frappe.local.conf` when it has one and
otherwise resolves from `SITES_PATH`, so the thread no longer needs its own init.
The init is retained because this package supports older v16 benches; its
docstring says so, and on a current bench it is inert.

**`FRAPPE_REDIS_QUEUE` handling in `config.py` — a v16 compatibility shim.** On `develop`,
`_get_common_site_config` calls `_apply_common_env_overrides`
(`frappe/config.py:123`) and honours `FRAPPE_REDIS_QUEUE`/`FRAPPE_REDIS_CACHE`
correctly. Only v16 reads `common_site_config.json` without env overrides. Draft 02
covers the part that *is* still missing on develop: the three socketio variables.
