# Feature: `frappe.runner` cannot listen on a unix socket

**Branch:** `develop` (verified at `34224e0128`, 2026-09-10)
**Component:** `frappe/runner.py`
**Type:** feature request

## The gap

`runner.main()` offers `--host` and `--port` only (`frappe/runner.py:508-509`), and
`WebServer.__init__` passes them straight to `uvicorn.Config`. There is no way to
bind a unix socket, so the runner cannot be used in a deployment that puts the web
server behind a unix socket rather than a loopback port.

This is a step back from what it replaces on two counts:

- gunicorn takes `--bind unix:/path/to.sock` natively, and that is how a
  socket-mode bench runs the web process today;
- the realtime half already supports it — `socketio_uds` is honoured by
  `RealtimeServer` (`frappe/realtime/server.py`), and was added precisely so
  realtime could avoid a TCP port.

So a bench that had both gunicorn and the Node realtime server on unix sockets
cannot move to the unified runner without reintroducing a TCP listener.

Why it matters beyond taste: several benches on one host is the common development
case, and unix sockets are what keeps them from colliding on ports. It is also one
less listener to firewall in production.

## Suggested fix

uvicorn already supports it; it is a matter of plumbing a flag through:

```python
parser.add_argument("--uds", help="unix socket to listen on; it wins over --host and --port")
```

with `Config` gaining a `uds: str | None = None` field, and `WebServer.__init__`
selecting the binding:

```python
if config.uds:
    with suppress(FileNotFoundError):
        os.unlink(config.uds)          # uvicorn binds without unlinking first
    binding = {"uds": config.uds}
else:
    binding = {"host": config.host, "port": config.port}
```

The `unlink` matters: uvicorn does not remove a stale socket file, so a process
killed uncleanly leaves one behind and the next bind fails.
`RealtimeServer.run()` already does this for the standalone path.

## Implementation

`patches/0001-runner-uds.patch` in
<https://github.com/Avunu/frappe-nix/tree/main/runtime> — in use and working.
