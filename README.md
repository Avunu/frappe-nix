# frappe-runtime

Frappe's Python runtime, extracted from upstream as a standalone package so it can run against a released Frappe without forking the framework

It replaces the Node `socket.io` server, and it can go further: one process serving the web app, realtime, the background jobs and the scheduler together.

## What this is

Upstream Frappe rewrote its realtime server in Python and then built on it — an ASGI adapter that serves realtime and the WSGI web app in one application, and a process runner that adds the RQ workers and the scheduler. That work lives on `develop`.

This package vendors it, pinned to a commit (see [`UPSTREAM`](UPSTREAM)), rewritten into a top-level `frappe_runtime` package, so a bench on a released Frappe can use it today.

| Upstream | Here |
| --- | --- |
| frappe/realtime/{auth,bridge,config,context,dispatch,handlers,registry,server,socket,util}.py | src/frappe_runtime/ |
| frappe/asgi.py | src/frappe_runtime/asgi.py |
| frappe/runner.py | src/frappe_runtime/runner.py |
| frappe/realtime/__init__.py | not taken — see below |
| frappe/tests/test_realtime_py.py | tests/test_frappe_runtime.py |

### What is deliberately not taken

`frappe/realtime/__init__.py` is the _publish_ half: `publish_realtime`, `emit_via_redis`, the room helpers, `SOCKETIO_SECRET_KEY`, and the two whitelisted endpoints the server calls back into. Frappe already ships all of it as `frappe/realtime.py`, unchanged and wire-compatible. Taking it would mean patching the framework for no gain, so we import from it instead.

The cost is two upstream conveniences this package does not provide: the `publish_to_*` named helpers, and a constant-time comparison of the socket secret. Both are additive patches to Frappe if you want them.

## Why `frappe_runtime` and not `frappe.realtime`

Upstream's module _replaces_ `frappe/realtime.py` with a `frappe/realtime/` package. A separate distribution cannot do that.

CPython's `FileFinder.find_spec` does prefer a package directory over a same-named module — but only within a single directory. In a bench, `frappe` reaches `sys.path` through an editable `.pth` pointing at `apps/frappe`, while this package installs into site-packages. Different path entries, so nothing here could ever shadow `frappe/realtime.py` there.

Handler authors are unaffected: `src/frappe_runtime/__init__.py` attaches `Socket` and `realtime` onto Frappe's `frappe.realtime` module at import, before app handler discovery runs. So upstream's documented import keeps working:

```python
from frappe.realtime import Socket, realtime


@realtime.on("project_subscribe")
async def project_subscribe(socket: Socket, project: str) -> None:
    if await socket.has_permission("Project", project):
        await socket.join(f"project:{project}")
```

Put that in `your_app/realtime/handlers.py`. See [`docs/handlers.md`](docs/handlers.md) for the full authoring guide (upstream's, verbatim).

## Running it

```sh
# web + realtime + jobs + scheduler, one process
frappe-runtime --uds /run/frappe/site.sock --job-threads 4

# realtime only, alongside an existing gunicorn
frappe-realtime
```

Run either from the bench root. `--uds` is local to this package (see `patches/0001-runner-uds.patch`); everything else is upstream's.

## Dependencies

Five beyond what Frappe already brings: `python-socketio`, `python-engineio`, `uvicorn`, `a2wsgi`, `httpx`. `frappe`, `redis`, `werkzeug`, `rq` and `watchdog` come from the bench environment.

The WebSocket transport is uvicorn's, which is the `websockets` library Frappe already depends on. `python-socketio` supplies only the Socket.IO / Engine.IO framing — which is what keeps the browser's `socket.io-client` working unchanged, long-polling handshake and all.

## Updating the pin

`src/` is committed rather than generated at build time, because `uv` builds this repo from a git source in a sandbox with no network. To move the pin:

```sh
scripts/sync-upstream.sh /path/to/frappe <ref>   # regenerates src/ and tests/
nix flake check                                   # drift check must pass
scripts/run-tests.sh /path/to/bench               # the 69 unit tests
```

Then update `FRAPPE_REF` in `UPSTREAM` and `frappe-upstream` in `flake.nix`.

`nix flake check` re-runs the extraction against the pinned upstream and requires byte-identical output, so a hand-edit to `src/` fails the build. Changes belong in `patches/` or `overlay/`.
