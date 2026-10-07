# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt


def add_fixture_header(response=None):
	"""T6: after_request. Marks every response, so a test can see the hook ran.

	frappe calls it with ``response`` and ``request`` as keywords, passing only the ones
	the function accepts.
	"""
	if response is not None:
		response.headers["X-Ironclad-Fixture"] = "1"
