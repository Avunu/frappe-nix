import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from frappe_nix_tools import apps_catalog
from frappe_nix_tools.commands.apps_catalog import DEFAULT_OUT

LISTED = [
	{
		"name": "helpdesk",
		"title": "Helpdesk",
		"description": "Well designed,\n  open source ticketing",
		"repo": "https://github.com/frappe/helpdesk",
		"categories": ["Featured", "Support"],
		"category": "Applications",
		"stars": 3184,
		"releases": [
			{
				"version": "1.30.1",
				"branch": "main",
				"frappe_core": ">=15.0.0,<17.0.0",
				"commit": "abc",
				"channel": "stable",
			}
		],
	},
	{
		"name": "india_compliance",
		"repo": "https://github.com/resilient-tech/india-compliance/",
		"releases": [],
	},
	{"name": "elsewhere", "repo": "https://gitlab.com/x/y", "releases": []},
	{"name": "frappe", "repo": "https://github.com/frappe/frappe", "releases": []},
]
ORG = [
	{"name": "helpdesk", "description": "dup"},
	{"name": "webshop", "description": "Webshop", "stargazers_count": 7},
	{"name": "frappe-books", "description": "Books"},
	{"name": "bench", "description": "the CLI"},
	{"name": "frappe"},
]
HEADS = {
	"frappe/helpdesk": ("develop", ["version-14"]),
	"resilient-tech/india-compliance": ("develop", ["version-16", "version-15"]),
	"frappe/webshop": ("develop", ["version-16", "version-15"]),
	"frappe/frappe-books": ("develop", ["version-16"]),
	"frappe/bench": ("develop", []),
}


class TestAppsCatalog(unittest.TestCase):
	def build(self):
		with (
			mock.patch.object(apps_catalog, "branches", side_effect=lambda repo: HEADS[repo]),
			mock.patch.object(
				apps_catalog,
				"app_name",
				side_effect=lambda repo, branch: repo.split("/")[1].replace("-", "_"),
			),
		):
			return {a["name"]: a for a in apps_catalog.build(LISTED, ORG)}

	def test_merge(self):
		apps = self.build()
		# frappe is in every bench; a non-GitHub listing has no branches to read; bench is
		# no app (no version-<n> branch).
		self.assertEqual(sorted(apps), ["frappe_books", "helpdesk", "india_compliance", "webshop"])
		helpdesk = apps["helpdesk"]
		self.assertTrue(helpdesk["listed"])
		self.assertEqual(helpdesk["description"], "Well designed, open source ticketing")
		self.assertEqual(helpdesk["branches"], ["version-14"])
		self.assertEqual(
			helpdesk["releases"],
			[{"version": "1.30.1", "branch": "main", "frappe_core": ">=15.0.0,<17.0.0", "channel": "stable"}],
		)
		self.assertIn("Official", helpdesk["categories"])
		self.assertEqual(apps["india_compliance"]["repo"], "resilient-tech/india-compliance")
		self.assertNotIn("Official", apps["india_compliance"]["categories"])
		webshop = apps["webshop"]
		self.assertEqual(
			(webshop["listed"], webshop["stars"], webshop["categories"]), (False, 7, ["Official"])
		)
		self.assertEqual(apps["frappe_books"]["default_branch"], "develop")

	def test_branches_parses_ls_remote(self):
		out = "ref: refs/heads/develop\tHEAD\nabc\tHEAD\nabc\trefs/heads/version-15\nabc\trefs/heads/version-16\nabc\trefs/heads/version-16-hotfix\n"
		with mock.patch.object(apps_catalog.subprocess, "run", return_value=mock.Mock(stdout=out)):
			self.assertEqual(
				apps_catalog.branches("frappe/erpnext"), ("develop", ["version-16", "version-15"])
			)

	def test_shipped(self):
		doc = json.loads(DEFAULT_OUT.read_text())
		names = {a["name"] for a in doc["apps"]}
		self.assertNotIn("frappe", names)
		for app in doc["apps"]:
			self.assertLessEqual(
				{"name", "repo", "listed", "branches", "default_branch", "releases"}, set(app), app["name"]
			)

	def test_sync_writes(self):
		with (
			tempfile.TemporaryDirectory() as tmp,
			mock.patch.object(apps_catalog, "registry", return_value=LISTED),
			mock.patch.object(apps_catalog, "org_repos", return_value=ORG),
			mock.patch.object(apps_catalog, "branches", side_effect=lambda repo: HEADS[repo]),
			mock.patch.object(apps_catalog, "app_name", side_effect=lambda repo, branch: "books"),
		):
			out = Path(tmp) / "apps.json"
			apps_catalog.sync(out)
			doc = json.loads(out.read_text())
			self.assertEqual(doc["sources"], ["github.com/frappe/marketplace", "github.com/frappe"])
			self.assertIn("books", {a["name"] for a in doc["apps"]})
