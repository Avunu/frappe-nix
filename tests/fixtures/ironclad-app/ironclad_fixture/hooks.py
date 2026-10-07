# Copyright (c) 2026, Avunu LLC and contributors
# For license information, please see license.txt

app_name = "ironclad_fixture"
app_title = "Ironclad Fixture"
app_publisher = "Avunu LLC"
app_description = "The app frappe-nix's Ironclad self-tests sync, test and check"
app_email = "mail@avu.nu"
app_license = "MIT"

required_apps = []

# T3: a doc_events handler.
doc_events = {
	"ToDo": {
		"on_update": "ironclad_fixture.events.todo_on_update",
	},
}

# T2: a scheduler job.
scheduler_events = {
	"daily": [
		"ironclad_fixture.tasks.daily",
	],
}

# T4: an extension class; testmap takes every public method defined on it.
extend_doctype_class = {
	"ToDo": ["ironclad_fixture.overrides.todo.IroncladToDo"],
}

# T6: a callable-valued request hook.
after_request = ["ironclad_fixture.request.add_fixture_header"]
