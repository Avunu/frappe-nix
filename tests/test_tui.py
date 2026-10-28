"""`frappe ui`: the spec the dev shell writes, lib/tui/frappe_nix_tui (the store's plugin)
over a fixture catalog and the shipped one, the command catalog the shell builds, and the
TUI itself driven headless (textual's run_test).

Run: python -I tests/test_tui.py <modules> <spec.json>
  <modules>   the TUI's modules (lib.tui.command's passthru.modules)
  <spec.json> the spec the shell wrote (lib.tui.script's passthru.spec)
with the TUI's interpreter (lib.tui.command's passthru.python). tests/tui.nix builds both
from a fixture bench's shell.
"""

import asyncio
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

if len(sys.argv) < 3:
	sys.exit("usage: test_tui.py <modules> <spec.json>")
sys.path.insert(0, sys.argv.pop(1))
SPEC = json.loads(Path(sys.argv.pop(1)).read_text())

import frappe_nix_tui as store
from devtui import commands, plugin, spec
from devtui.app import DevTui
from devtui.store import StoreView
from textual.widgets import DataTable, Input

CATALOG = {
	"apps": [
		{
			"name": "erpnext",
			"repo": "frappe/erpnext",
			"title": "ERPNext",
			"listed": True,
			"category": "Applications",
			"branches": ["version-16", "version-15"],
			"default_branch": "develop",
			"releases": [],
		},
		{
			"name": "helpdesk",
			"repo": "frappe/helpdesk",
			"title": "Helpdesk",
			"listed": True,
			"branches": ["version-14"],
			"default_branch": "develop",
			"releases": [
				{
					"version": "1.30.1",
					"branch": "main",
					"frappe_core": ">=15.116.1,<17.0.0",
					"dependencies": {"telephony": ">=0.0.1,<1.0.0"},
					"channel": "stable",
				},
				{
					"version": "1.22.2",
					"branch": "develop",
					"frappe_core": ">=16.0.0-dev,<18.0.0",
					"dependencies": {"telephony": ">=0.0.1,<1.0.0"},
					"channel": "nightly",
				},
			],
		},
		{
			"name": "telephony",
			"repo": "frappe/telephony",
			"listed": True,
			"branches": [],
			"default_branch": "develop",
			"releases": [{"version": "0.1.0", "branch": "develop", "frappe_core": ">=15.0.0,<18.0.0"}],
		},
		{
			"name": "india_compliance",
			"repo": "resilient-tech/india-compliance",
			"listed": True,
			"categories": ["Localization"],
			"branches": ["version-16", "version-15"],
			"default_branch": "develop",
			"releases": [],
		},
		{
			"name": "webshop",
			"repo": "frappe/webshop",
			"listed": False,
			"categories": ["Official"],
			"branches": ["version-15"],
			"default_branch": "develop",
			"releases": [],
		},
	]
}


def bench(version="16.53.0", apps=("frappe", "erpnext")):
	root = Path(tempfile.mkdtemp())
	init = root / "apps" / "frappe" / "frappe" / "__init__.py"
	init.parent.mkdir(parents=True)
	init.write_text(f'import os\n__version__ = "{version}"\n')
	(root / "sites").mkdir()
	(root / "sites" / "apps.txt").write_text("".join(f"{a}\n" for a in apps))
	(root / "catalog.json").write_text(json.dumps(CATALOG))
	return root


def product(root, catalog=None):
	data = {
		**SPEC,
		"plugin": {**SPEC["plugin"], "config": {"catalog": str(catalog or root / "catalog.json")}},
	}
	return plugin.load(spec.load(data, {"benchRoot": str(root), "port": "8000"}))


class VersionTest(unittest.TestCase):
	def test_satisfies(self):
		self.assertTrue(store.satisfies("16.53.0", ">=15.116.1,<17.0.0"))
		self.assertFalse(store.satisfies("17.0.0", ">=15.116.1,<17.0.0"))
		self.assertFalse(store.satisfies("17.0.0-dev", ">=15.116.1,<17.0.0"))
		self.assertTrue(store.satisfies("17.0.0-dev", ">=16.0.0-dev,<18.0.0"))
		self.assertFalse(store.satisfies("16.0.0-dev", ">=16.0.0"))
		self.assertTrue(store.satisfies("16.1.0", "~=16.0"))
		self.assertTrue(store.satisfies("16.1.0", ""))


class StoreTest(unittest.TestCase):
	def setUp(self):
		self.root = bench()
		self.p = product(self.root)

	def tearDown(self):
		shutil.rmtree(self.root)

	def test_spec(self):
		s = spec.load(SPEC, {"benchRoot": str(self.root), "port": "8023"})
		self.assertEqual(s.cli, "frappe")
		self.assertEqual(s.root, str(self.root))
		self.assertEqual(s.site.url, "http://127.0.0.1:8023")
		self.assertIn("frappe ui", [c.command for c in s.banner.commands])
		self.assertEqual(s.commands[1].cwd, f"{self.root}/sites")

	def test_branches(self):
		c = self.p.choices
		self.assertEqual(c["erpnext"].branch, "version-16")
		self.assertEqual((c["helpdesk"].branch, c["helpdesk"].needs), ("main", ("telephony",)))
		self.assertEqual(c["telephony"].branch, "develop")
		self.assertEqual(c["webshop"].branch, "")
		self.assertIn("nothing for Frappe 16 (version-15)", c["webshop"].why)
		items = self.p.catalog().by_id
		self.assertFalse(items["webshop"].available)
		self.assertEqual(items["india_compliance"].group, "resilient-tech")
		self.assertEqual(self.p.installed(), {"frappe", "erpnext"})

	def test_develop(self):
		root = bench("17.0.0-dev")
		try:
			c = product(root).choices
			self.assertEqual(c["erpnext"].branch, "develop")
			self.assertEqual(c["helpdesk"].branch, "develop")
		finally:
			shutil.rmtree(root)

	def test_add(self):
		closure = self.p.closure(["helpdesk"], [])
		self.assertEqual(closure.brings, ("telephony",))
		steps = [s.argv for s in self.p.add(["helpdesk", "india_compliance"], [])]
		self.assertEqual(
			steps,
			[
				("frappe", "get-app", "--branch", "develop", "telephony"),
				("frappe", "get-app", "--branch", "main", "helpdesk"),
				("frappe", "get-app", "--branch", "version-16", "resilient-tech/india-compliance"),
			],
		)
		# Installed, or not in the catalog for this Frappe: never added.
		self.assertEqual(self.p.add(["erpnext"], []), [])
		self.assertEqual(self.p.closure(["webshop"], []).unavailable, ("webshop",))
		self.assertEqual(self.p.after_add()[0].argv, ("devtui:rebuild",))

	def test_remove(self):
		self.assertEqual(
			[s.argv for s in self.p.remove(["erpnext"])],
			[("frappe", "uninstall-app", "erpnext", "--yes"), ("frappe", "remove-app", "erpnext")],
		)

	def test_form(self):
		form = self.p.forms()[0]
		self.assertEqual(form.build({"url": "owner/app"}), ["frappe", "get-app", "owner/app"])
		self.assertEqual(
			form.build({"url": "owner/app", "branch": "version-16"}),
			["frappe", "get-app", "--branch=version-16", "owner/app"],
		)

	def test_shipped_catalog(self):
		p = product(self.root, SPEC["plugin"]["config"]["catalog"])
		items = p.catalog().by_id
		self.assertGreater(len(items), 100)
		self.assertNotIn("frappe", items)
		for name in ("erpnext", "hrms", "helpdesk", "webshop", "newsletter"):
			self.assertIn(name, items)
		self.assertEqual(p.choices["erpnext"].branch, "version-16")

	def test_command_catalog(self):
		own = commands.load_file(spec.load(SPEC, {}).commands[0].file)
		self.assertEqual((own.name, own.source), ("frappe", "frappe-nix"))
		get_app = next(c for c in own.walk() if c.path == ("get-app",))
		self.assertTrue(get_app.raw)
		self.assertEqual(
			commands.argv("frappe", get_app, {"args": "--branch version-16 hrms"}),
			["frappe", "get-app", "--branch", "version-16", "hrms"],
		)


class AppTest(unittest.IsolatedAsyncioTestCase):
	async def asyncSetUp(self):
		# unittest runs the loop in debug mode, which logs every callback over
		# 0.1s: a textual frame often is one.
		asyncio.get_running_loop().slow_callback_duration = 60

	async def test_store(self):
		root = bench()
		try:
			data = {k: v for k, v in SPEC.items() if k not in ("stack", "commands")}
			data["plugin"] = {**SPEC["plugin"], "config": {"catalog": str(root / "catalog.json")}}
			app = DevTui(data, {"benchRoot": str(root), "port": "8000"})
			async with app.run_test(size=(160, 50)) as pilot:
				await pilot.press("4")
				view = app.query_one(StoreView)
				view.query_one("#store-search", Input).value = "helpdesk"
				for _ in range(100):
					await pilot.pause(0.05)
					if len(view.rows) == 1:
						break
				self.assertEqual(view.rows[0].id, "helpdesk")
				view.query_one("#store-table", DataTable).focus()
				await pilot.press("space")
				self.assertEqual(view.adds, ["helpdesk"])
				self.assertEqual(view.closure().brings, ("telephony",))
		finally:
			shutil.rmtree(root)


if __name__ == "__main__":
	unittest.main(argv=[sys.argv[0], "-v"])
