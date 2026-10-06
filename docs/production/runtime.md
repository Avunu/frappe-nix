---
title: The unified runtime
description: One frappe-runtime process per bench serves the web app, realtime, background jobs and the scheduler, how a bench gets it, and how to go back to the split processes.
order: 2
tags: [runtime, realtime, socketio, asgi]
updated: 2026-10-06
---

By default each bench runs a single [`frappe-runtime`](https://github.com/Avunu/frappe-nix/tree/main/runtime) process, a hard fork maintained in this repository under `runtime/`. It serves the web app, realtime, the background jobs and the scheduler together, in place of gunicorn or `bench serve`, the Node `socket.io` server, one worker per queue and `bench schedule`.

It began as upstream Frappe's own asyncio and uvicorn port, made to run against a released Frappe and fixed where the upstream runner did not work. The defects it fixed are drafted in [`runtime/docs/upstream-issues/`](https://github.com/Avunu/frappe-nix/tree/main/runtime/docs/upstream-issues).

Two things follow beyond the process count:

- **Node leaves the runtime closure entirely.** It stays a build-time dependency for `bench build`.
- **nginx loses its loopback `:80` listener**, along with the `networking.hosts` pin that resolved each site's FQDN to `127.0.0.1`. Those existed only because the Node realtime server validated sessions by making an HTTP request back to the site's own name, and node's `fetch` cannot speak a unix socket. The Python runtime validates in-process against the WSGI app.

## Where it runs

| Place                 | What runs                                                                                                                                                                        |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The development shell | A `runtime` process on a unix socket behind nginx, with `--dev` (reload on a Python change, and serves `/assets` and `/files`) and `runtime.jobThreads` job threads (default 2). |
| `services.frappe`     | One `frappe-<SITE>` systemd unit per site. See [Run Frappe as a NixOS service](nixos-service.md).                                                                                |
| Container images      | The `runtime` image runs `frappe-runtime --host 0.0.0.0 --port 8000 --job-threads 4`, without `--dev`. See [Build production images](images.md).                                 |

The package also installs a second command, `frappe-realtime`, which runs the realtime server alone, for a stack that keeps gunicorn.

```bash
# web + realtime + jobs + scheduler, one process
frappe-runtime --uds /run/frappe/site.sock --job-threads 4

# realtime only, alongside an existing gunicorn
frappe-realtime
```

Run either from the bench root. The runner changes into `sites/` itself. `--uds` is this fork's addition, and it wins over `--host` and `--port`.

## How a bench gets it

Nothing to do for a new bench: `frappe-init` writes the dependency into `pyproject.toml` from the template and locks it.

Nothing to do for an existing bench either: the dev shell adds it on entry. A bench from before the runtime evaluates and opens as it always did, running the split processes for that one session. `enterShell` reconciles the workspace root and re-locks (see [Upgrading frappe-nix](../development/upgrading.md)). Commit `pyproject.toml` and `uv.lock`, re-enter the shell, and `devenv up` runs the runtime.

What lands is three things, the same way `frappe-init` lands `frappe-bench` and `setuptools`:

- `frappe-runtime` in `[project].dependencies`;
- a `[tool.uv.sources]` entry pointing at this repository's `runtime/` subdirectory;
- `hatchling` in `[tool.uv.extra-build-dependencies]`, because uv builds without isolation here and the package's own `build-system.requires` is not enough.

The reconciler (`nix run github:Avunu/frappe-nix -- -y`) does the same and remains the way to do it from outside a shell.

The declaration exists for uv's resolver and is a placeholder, not a version pin. frappe-nix points uv2nix's `srcOverrides` at its own `runtime/` directory, so the code that is actually built is whatever this repository ships, and a bump is `nix flake update frappe-nix`, with no relock in any bench. The entry has to exist because uv2nix indexes its package set by `uv.lock`, and `srcOverrides` can only swap the source of a package already in that set.

The seam is that only the _source_ is overridden. Dependency metadata still comes from `uv.lock`. A frappe-runtime release that adds a new dependency does need one relock:

```bash
nix run .#relock -- --upgrade-package frappe-runtime
```

Note the flag. A plain `uv lock` keeps an already resolved git revision and reports success without changing anything.

Set `runtime.src = null` to hand version control back to `uv.lock`.

## Going back

`runtime.enable = false` restores the split processes and the Node realtime server, in both the dev shell and `services.frappe`. Everything that shape needs is still there and still tested: `socketio.socketPath`, `socketio.port`, `web.workers`, the loopback listener and the per-image container set. `checks.socket` covers the split shape, and `checks.socket-runtime` covers the unified one.

## Realtime handlers

Custom apps add their own realtime events by registering handlers in `<APP>/realtime/handlers.py`, with the `@realtime.on` decorator. The runtime imports that module for every app installed on the connecting site, so a handler runs only for sockets on sites that have its app installed. The complete authoring guide is [`runtime/docs/handlers.md`](https://github.com/Avunu/frappe-nix/blob/main/runtime/docs/handlers.md).

```python
from frappe.realtime import Socket, realtime


@realtime.on("project_subscribe")
async def project_subscribe(socket: Socket, project: str) -> None:
    if await socket.has_permission("Project", project):
        await socket.join(f"project:{project}")
```

Upstream's documented import keeps working even though this package is called `frappe_runtime` and not `frappe.realtime`. A separate distribution cannot replace Frappe's own `frappe/realtime.py` with a package, so `frappe_runtime` attaches `Socket` and `realtime` onto Frappe's `frappe.realtime` module at import, before app handler discovery runs.

## Running more than one process

By default rooms live in the process's own memory, which is correct while one process serves a site. Set `socketio_redis_manager: true` in `common_site_config.json` to back them with Redis instead, so a handler's `socket.emit(room=...)` reaches sockets on every process and not only its own. It is off by default: with a single process it buys nothing and adds a Redis round trip to every handler emit.

Events published by Frappe are unaffected either way. Every process runs its own Redis bridge and already receives them directly, so the bridge emits with `ignore_queue=True`. Without that, a shared manager would re-publish each event once per process, and every client would see it N times.

> [!WARNING]
> The manager is necessary but **not sufficient** to run several processes. Engine.IO keeps its session table in memory, and the browser's `socket.io-client` opens on HTTP long-polling, so the load balancer must also pin a client to one process for the handshake to complete. Solve that before raising the process count.

## Dependencies and tests

The package adds five dependencies beyond what Frappe already brings: `python-socketio`, `python-engineio`, `uvicorn`, `a2wsgi` and `httpx`. `frappe`, `redis`, `werkzeug`, `rq` and `watchdog` come from the bench environment. The WebSocket transport is uvicorn's, which is the `websockets` library Frappe already depends on. `python-socketio` supplies only the Socket.IO and Engine.IO framing, which keeps the browser's `socket.io-client` working unchanged, long-polling handshake and all.

`src/` is canonical and edited directly. The unit suite needs a real bench, because `frappe` must be importable:

```bash
runtime/scripts/run-tests.sh /path/to/bench   # the unit suite; needs a bench
nix flake check                               # from the repository root: builds the package
```
