# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: MIT. See LICENSE
"""Resolve realtime server config.

Reuses ``frappe.get_common_site_config()`` (the same ``common_site_config.json``
the Node ``node_utils.get_conf`` reads) instead of re-porting the JSON parse.
Config comes only from common_site_config.json / site_config.json — no env vars.

``frappe`` is imported lazily so this module can be imported in tests without a
configured bench.
"""

import os
from dataclasses import dataclass

# node_utils default
DEFAULT_SOCKETIO_PORT = 9000

# Matches frappe.config._get_site_config fallback; used only if common_site_config.json
# is silent on redis_queue, which should not happen in a real bench.
DEFAULT_REDIS_QUEUE = "redis://127.0.0.1:11311"

# Threads for the blocking work: connect auth, permission checks, sync handlers.
# A bench with more clients sets socketio_worker_threads.
DEFAULT_WORKER_THREADS = 4


@dataclass(frozen=True)
class RealtimeConfig:
	port: int
	redis_queue: str
	uds: str | None = None
	default_site: str | None = None
	developer_mode: bool = False
	webserver_port: int | None = None
	webserver_host: str | None = None
	worker_threads: int | None = None
	# Absolute, because serve() changes into sites/ before it builds the server, and a
	# relative path would then point one level too deep.
	sites_path: str = "sites"
	# Sharing a process with the web app: call back in-process rather than looping
	# HTTP back into ourselves. This is a property of the process, not of the site
	# config; the process that embeds realtime sets it. See frappe.asgi.
	embedded: bool = False


def _env_or_conf(conf, env_var: str, key: str):
	"""Environment first, then common_site_config.json.

	frappe.get_common_site_config() is a plain JSON read. Unlike
	frappe.config._get_site_config, which resolves FRAPPE_REDIS_QUEUE and friends,
	it applies no environment overrides at all -- so a bench that configures its
	services through the environment is invisible to it.

	The Node server this replaces did read them (node_utils.js get_conf: FRAPPE_SITE,
	FRAPPE_REDIS_QUEUE, FRAPPE_SOCKETIO_PORT, FRAPPE_SOCKETIO_UDS), so dropping them
	is a regression rather than a simplification. The failure is silent and total:
	a bench whose redis lives on a unix socket falls back to redis://127.0.0.1:11311,
	the bridge subscribes to a redis nobody publishes to, and no event is ever
	delivered.
	"""
	return os.environ.get(env_var) or conf.get(key)


def get_config(sites_path: str | None = None, embedded: bool = False) -> RealtimeConfig:
	"""Build the realtime config from common_site_config.json."""
	import frappe

	sites_path = os.path.abspath(sites_path or getattr(frappe.local, "sites_path", None) or "sites")
	conf = frappe.get_common_site_config(sites_path=sites_path)

	webserver_port = conf.get("webserver_port")
	return RealtimeConfig(
		port=int(_env_or_conf(conf, "FRAPPE_SOCKETIO_PORT", "socketio_port") or DEFAULT_SOCKETIO_PORT),
		redis_queue=_env_or_conf(conf, "FRAPPE_REDIS_QUEUE", "redis_queue") or DEFAULT_REDIS_QUEUE,
		uds=_env_or_conf(conf, "FRAPPE_SOCKETIO_UDS", "socketio_uds") or None,
		default_site=_env_or_conf(conf, "FRAPPE_SITE", "default_site") or None,
		developer_mode=bool(conf.get("developer_mode")),
		webserver_port=int(webserver_port) if webserver_port else None,
		webserver_host=conf.get("webserver_host") or None,
		worker_threads=int(conf.get("socketio_worker_threads") or DEFAULT_WORKER_THREADS),
		sites_path=sites_path,
		embedded=embedded,
	)
