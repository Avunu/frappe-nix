# Copyright (c) 2026, Example Org and contributors
# For license information, please see license.txt

app_name = "standards_fixture"
app_title = "Standards Fixture"
app_publisher = "Example Org"
app_description = "The app frappe-nix's app standards self-tests sync, test and check"
app_email = "apps@example.org"
app_license = "MIT"

required_apps = []

# T3: a doc_events handler.
doc_events = {
	"ToDo": {
		"on_update": "standards_fixture.events.todo_on_update",
	},
}

# T2: a scheduler job.
scheduler_events = {
	"daily": [
		"standards_fixture.tasks.daily",
	],
}

# T4: an extension class; testmap takes every public method defined on it.
extend_doctype_class = {
	"ToDo": ["standards_fixture.overrides.todo.FixtureToDo"],
}

# T6: a callable-valued request hook.
after_request = ["standards_fixture.request.add_fixture_header"]
