# Copyright (c) 2026, Example Org and contributors
# For license information, please see license.txt


def add_fixture_header(response=None):
	"""T6: after_request. Marks every response, so a test can see the hook ran.

	frappe calls it with ``response`` and ``request`` as keywords, passing only the ones
	the function accepts (test_utils' static_analysis reports an unused parameter).
	"""
	if response is not None:
		response.headers["X-Standards-Fixture"] = "1"
