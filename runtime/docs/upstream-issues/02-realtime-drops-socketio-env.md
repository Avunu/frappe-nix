# Python realtime server ignores the socketio env vars the Node server honoured

**Branch:** `develop` (verified at `34224e0128`, 2026-09-10)
**Component:** `frappe/realtime/config.py`
**Impact:** a bench configured through the environment silently gets defaults —
wrong port, or a TCP listener where a unix socket was asked for.

## What happens

`node_utils.js` `get_conf()` applied five environment overrides. The Python port
that replaces it applies none of its own — `frappe/realtime/config.py` contains no
`os.environ` reads at all, and its docstring states the intent:

> Config comes only from common_site_config.json / site_config.json — no env vars.

Two of the five survive by accident, because `get_common_site_config()` applies
them itself (`_apply_common_env_overrides`, `frappe/config.py:123`):

| Variable | Node `node_utils.js` | Python port |
| --- | --- | --- |
| `FRAPPE_REDIS_QUEUE` | ✅ | ✅ via `get_common_site_config` |
| `FRAPPE_REDIS_CACHE` | ✅ | ✅ via `get_common_site_config` |
| `FRAPPE_SOCKETIO_UDS` | ✅ | ❌ dropped |
| `FRAPPE_SOCKETIO_PORT` | ✅ | ❌ dropped |
| `FRAPPE_SITE` (→ `default_site`) | ✅ | ❌ dropped |

So a deployment that set `FRAPPE_SOCKETIO_UDS` to put realtime on a unix socket
now gets `uds=None` and a TCP listener on port 9000 instead, with no warning. The
`socketio_uds` support this replaces was added for exactly that deployment shape.

## Reproduction

```sh
cd /path/to/bench/sites
FRAPPE_SOCKETIO_UDS=/run/frappe/socketio.sock \
FRAPPE_SOCKETIO_PORT=9001 \
python -c "from frappe.realtime.config import get_config; c = get_config(); print(c.uds, c.port)"
# None 9000
```

## Root cause

`get_config()` (`frappe/realtime/config.py:47`) reads each value straight from the
merged config dict:

```python
port=int(conf.get("socketio_port") or DEFAULT_SOCKETIO_PORT),
uds=conf.get("socketio_uds") or None,
default_site=conf.get("default_site") or None,
```

`get_common_site_config()` only injects the two redis variables, so the three
socketio ones never reach `conf`.

## Suggested fix

Either read them in `get_config()`:

```python
def _env_or_conf(conf, env_var, key):
    return os.environ.get(env_var) or conf.get(key)
```

applied to `FRAPPE_SOCKETIO_PORT`/`socketio_port`,
`FRAPPE_SOCKETIO_UDS`/`socketio_uds` and `FRAPPE_SITE`/`default_site` — or extend
`_apply_common_env_overrides` so the whole set lands in one place, which seems
closer to the intent given the redis pair already lives there.

If dropping them is deliberate, it is a breaking change for anyone configuring a
bench through the environment and deserves a release note; the Node server it
replaces read them for years.

## Workaround

`patches/0002-config-env-overrides.patch` in
<https://github.com/Avunu/frappe-nix/tree/main/runtime>.
