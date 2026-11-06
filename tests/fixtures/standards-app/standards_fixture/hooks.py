# Copyright (c) 2026, Example Org and contributors
# For license information, please see license.txt

app_name = "standards_fixture"
app_title = "Standards Fixture"
app_publisher = "Example Org"
app_description = "The app frappe-nix's app standards self-tests sync, test and check"
app_email = "apps@example.org"
app_license = "MIT"

required_apps = []

# For the logo in the About dialog; apps_screen.not_listed keeps it off the apps screen.
add_to_apps_screen = [
	{
		"name": "standards_fixture",
		"logo": "/assets/standards_fixture/images/standards_fixture-logo.svg",
		"title": "Standards Fixture",
		"route": "/desk",
		"has_permission": "standards_fixture.apps_screen.not_listed",
	}
]

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
