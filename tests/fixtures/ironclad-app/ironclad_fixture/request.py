def add_fixture_header(response=None, request=None):
	"""T6: after_request. Marks every response, so a test can see the hook ran."""
	if response is not None:
		response.headers["X-Ironclad-Fixture"] = "1"
