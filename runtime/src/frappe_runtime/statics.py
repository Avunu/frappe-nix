# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: MIT. See LICENSE
"""/assets and /files for a runtime that sends its own static files (--dev).

Forked from frappe.app.application_with_statics and
frappe.middlewares.StaticDataMiddleware, for the one place they disagree with
this runtime: which site a /files request belongs to."""

import os
import threading
from pathlib import Path

import frappe.app
from frappe.middlewares import StaticDataMiddleware
from frappe.utils import cstr, get_site_name
from werkzeug.middleware.shared_data import SharedDataMiddleware

from frappe_runtime.util import is_site


class SiteFilesMiddleware(StaticDataMiddleware):
	"""frappe's /files server, taking the site the way frappe.app.init_request does:
	frappe.app._site, else X-Frappe-Site-Name, else the Host.

	The parent skips the header, and the header is how default_site_middleware sends
	a Host that names no site to the default site. So http://localhost:<port>/files/x
	looked in sites/localhost/public/files, and every /files URL of the default site
	failed -- the website theme's stylesheet among them, which left /login unstyled.

	Two more departures from the parent:

	- A file that is not there falls through to the app, the way try_files does
	  behind nginx, where the parent raises NotFound: this deep, outside frappe's
	  error handling, the exception reaches the server as a 500.
	- The environ is per thread. The parent keeps it on the instance that every
	  request thread shares, so two requests at once could swap sites.
	"""

	_request = threading.local()

	def get_directory_loader(self, directory):
		def loader(path):
			environ = self._request.environ
			site = get_site_name(
				frappe.app._site or environ.get("HTTP_X_FRAPPE_SITE_NAME") or environ.get("HTTP_HOST")
			)
			# The header and the Host both come from the client: only a site of this
			# bench picks a directory.
			if os.path.basename(site) != site or not is_site(directory, site):
				return None, None
			files_path = Path(directory) / site / "public" / "files"
			file = (files_path / cstr(path)).resolve()
			if not file.is_relative_to(files_path) or not file.is_file():
				return None, None
			return file.name, self._opener(file)

		return loader

	def __call__(self, environ, start_response):
		self._request.environ = environ
		return super().__call__(environ, start_response)


def application_with_statics(app, sites_path: str):
	"""frappe.app.application_with_statics, around `app`, with /files from
	SiteFilesMiddleware. Unlike frappe's, it leaves frappe.app.application alone."""
	app = SharedDataMiddleware(app, {"/assets": os.path.join(sites_path, "assets")})
	return SiteFilesMiddleware(app, {"/files": os.path.abspath(sites_path)})
