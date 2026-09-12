# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: MIT. See LICENSE
"""Small helpers for the realtime connect path: header reads, hostnames, URLs,
and site resolution. Kept separate so auth.py stays focused on the pipeline."""

import os
from urllib.parse import urlsplit

from frappe_runtime.config import RealtimeConfig


def read_header(environ: dict, name: str) -> str | None:
	"""Read an HTTP header from a WSGI environ (HTTP_FOO style)."""
	return environ.get("HTTP_" + name.upper().replace("-", "_"))


def get_hostname(url: str | None) -> str | None:
	"""hostname without scheme, userinfo, or port."""
	if not url:
		return None
	if "://" not in url:
		url = f"//{url}"
	return urlsplit(url).hostname


def is_site(sites_path: str, name: str | None) -> bool:
	"""Whether <sites_path>/<name> is a site of this bench -- the same test
	frappe.utils.get_sites applies: a directory holding a site_config.json."""
	return bool(name) and os.path.isfile(os.path.join(sites_path, name, "site_config.json"))


def resolve_site_name(environ: dict, config: RealtimeConfig) -> str | None:
	"""Resolve the site name: X-Frappe-Site-Name if the proxy set it, else the
	Origin's (or Host's) hostname when that names a site on this bench, else the
	default site.

	Derived from authenticate.js get_site_name, which fell back to default_site
	only for a Host of localhost/127.0.0.1. Any hostname that is no site falls
	back here, so that the socket lands on the same site the page came from:
	the web half sends such a request to the default site too
	(default_site_middleware), and a page served as the default site from
	http://myhost.lan:8000 would otherwise connect its socket to a site named
	myhost.lan, which does not exist.
	"""
	site_header = read_header(environ, "X-Frappe-Site-Name")
	if site_header:
		return get_hostname(site_header)
	candidate = get_hostname(read_header(environ, "Origin")) or get_hostname(read_header(environ, "Host"))
	if config.default_site and not is_site(config.sites_path, candidate):
		return config.default_site
	return candidate


def default_site_middleware(app, config: RealtimeConfig):
	"""Send a request whose Host names no site on this bench to the default site.

	frappe.app.init_request picks the site from frappe.app._site, else the
	X-Frappe-Site-Name header, else the Host's hostname -- and a hostname that is
	no site is a 404 that, in developer mode, lists the sites of the bench. _site
	is set only by frappe.app.serve(), the entry point of `bench serve`, which
	pinned it from FRAPPE_SITE (or default_site) and so sent every request to that
	one site. This runtime imports the WSGI app directly and never calls serve(),
	which is how a dev bench lost its default site: http://localhost:<port> is a
	Host of "localhost", and there is no site by that name.

	Restored here as a header, and only for a Host that names no site: a Host
	that does keeps its site, so a bench with several of them still serves each
	by name, and a proxy that already set the header is left alone. Without a
	default site (multi-tenancy) this is a no-op.
	"""
	if not config.default_site:
		return app

	def with_default_site(environ, start_response):
		if not environ.get("HTTP_X_FRAPPE_SITE_NAME") and not is_site(
			config.sites_path, get_hostname(environ.get("HTTP_HOST"))
		):
			environ["HTTP_X_FRAPPE_SITE_NAME"] = config.default_site
		return app(environ, start_response)

	return with_default_site


def get_url(origin: str | None, path: str, config: RealtimeConfig) -> str:
	"""Build the web-process URL for a request. Port of realtime/utils.js get_url."""
	if config.webserver_host and config.webserver_port:
		base = config.webserver_host
		if "://" not in base:
			base = f"http://{base}"
		parts = urlsplit(base)
		if parts.port is None:
			base = f"{parts.scheme}://{parts.netloc}:{config.webserver_port}"
		else:
			base = f"{parts.scheme}://{parts.netloc}"
		return base + (path or "")
	url = origin or ""
	if config.developer_mode and config.webserver_port:
		parts = url.split(":")
		protocol = parts[0] if len(parts) > 0 else ""
		host = parts[1] if len(parts) > 1 else ""
		url = f"{protocol}:{host}:{config.webserver_port}"
	return url + (path or "")
