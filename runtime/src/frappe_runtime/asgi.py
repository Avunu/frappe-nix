# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: MIT. See LICENSE
"""ASGI entry point: ``uvicorn frappe_runtime.asgi:application``.

Realtime answers "/socket.io/", the web app answers the other paths. Set
FRAPPE_SERVE_ASSETS to also send /assets and /files, as when there is no proxy.
"""

import os

from a2wsgi import WSGIMiddleware
from frappe.app import application as wsgi_application
from frappe.utils.data import sbool

from frappe_runtime.config import get_config as get_socketio_config
from frappe_runtime.server import RealtimeServer
from frappe_runtime.statics import application_with_statics
from frappe_runtime.util import default_site_middleware

DEFAULT_WEB_THREADS = 8
web_threads = int(os.environ.get("FRAPPE_WEB_THREADS") or DEFAULT_WEB_THREADS)

socketio_config = get_socketio_config(embedded=True)

if sbool(os.environ.get("FRAPPE_SERVE_ASSETS", False)):
	# Inside default_site_middleware, so /files sees the site it picks. See statics.py.
	wsgi_application = application_with_statics(wsgi_application, socketio_config.sites_path)

# `bench serve` pinned the site from FRAPPE_SITE; the direct import above does
# not, so a Host that names no site (http://localhost:<port>) gets the default
# site the same way the realtime half already resolves it. See util.py.
wsgi_application = default_site_middleware(wsgi_application, socketio_config)
application = RealtimeServer(
	socketio_config, other_asgi_app=WSGIMiddleware(wsgi_application, workers=web_threads)
).app
