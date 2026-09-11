# SPDX-License-Identifier: MIT
"""Frappe's Python runtime, forked from upstream as a standalone package.

Forked from frappe/frappe at 757f127a10 (frappe/realtime/, frappe/asgi.py,
frappe/runner.py), MIT. This is a hard fork: the source here is canonical and is
edited directly. The per-file Frappe copyright headers are retained.

`frappe_runtime` carries three things lifted out of the Frappe tree:

* the Socket.IO realtime server (``server``, ``auth``, ``dispatch``, ``socket``,
  ``registry``, ``bridge``, ``handlers``, ``config``, ``context``, ``util``),
* the ASGI adapter that serves realtime and the WSGI web app together (``asgi``),
* the process runner that adds the RQ workers and the scheduler (``runner``).

It deliberately does **not** carry Frappe's publish half. ``publish_realtime``,
``emit_via_redis``, the room helpers and the two whitelisted endpoints stay in
Frappe's own ``frappe/realtime.py``, unmodified — which is what lets this package
run against a stock Frappe checkout.

Upstream names this package ``frappe.realtime``. We cannot: a separate
distribution installs into site-packages, while ``frappe`` itself reaches
``sys.path`` through an editable ``.pth`` pointing at ``apps/frappe``. Those are
different path entries, so a ``frappe/realtime/`` directory here could never
shadow ``frappe/realtime.py`` there. Hence a top-level name, plus the compat shim
below so that app authors still write upstream's documented import.
"""

from frappe_runtime.registry import realtime
from frappe_runtime.socket import Socket

__all__ = ["Socket", "realtime"]


def _install_compat_shim() -> None:
	"""Publish ``Socket`` and ``realtime`` on Frappe's ``frappe.realtime`` module.

	Upstream's handler API is ``from frappe.realtime import Socket, realtime``, and
	that is what its README tells app authors to write. Stock Frappe's
	``frappe/realtime.py`` has neither name, so we attach them here.

	This runs at package import, which is before ``server.load_handlers()`` reaches
	``discover_app_handlers()`` — so every ``<app>/realtime/handlers.py`` sees the
	names whether it imports them from ``frappe.realtime`` or from here. It also
	means an app written against this package keeps working unchanged if upstream
	ever ships its own ``frappe.realtime`` package.
	"""
	import frappe.realtime

	frappe.realtime.Socket = Socket
	frappe.realtime.realtime = realtime


_install_compat_shim()
