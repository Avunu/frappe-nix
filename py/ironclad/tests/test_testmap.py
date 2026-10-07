import importlib
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import ClassVar

from ironclad.bench import script, testmap_probe
from ironclad.commands.testmap import testmap_failures, testmap_markdown

HOOKS = {
	"app_name": ["demo"],
	"scheduler_events": {"daily": ["demo.tasks.daily"], "cron": {"0 * * * *": ["demo.tasks.hourly"]}},
	"doc_events": {
		"ToDo": {"on_update": ["demo.events.todo_on_update"]},
		"*": {"validate": ["frappe.other.validate", "demo.events.any_validate"]},
	},
	"extend_doctype_class": {"ToDo": ["demo.overrides.DemoToDo"]},
	"override_whitelisted_methods": {"frappe.desk.x": ["demo.api.x"]},
	"permission_query_conditions": {"ToDo": ["demo.perm.todo_query"]},
	"after_request": ["demo.request.after"],
	"after_install": ["demo.install.after_install"],
	"jinja": {"methods": ["demo.jinja_methods"], "filters": ["demo.filters.upper"]},
	"website_context": {"favicon": ["/assets/demo/icon.png"], "ctx": ["/api/method/demo.api.ctx?x=1"]},
	"additional_timeline_content": {"*": ["demo.timeline.content"]},
}

PACKAGE = {
	"__init__.py": "",
	"hooks.py": "app_name = 'demo'\n",
	"api.py": """\
		import frappe
		from frappe import whitelist as wl


		@frappe.whitelist()
		def ping(name: str = "world") -> str:
			\"\"\"Tested.\"\"\"
			return f"pong {name}"


		@frappe.whitelist(allow_guest=True)
		def legacy():
			return "legacy"


		@wl
		def aliased():
			return 1


		def plain():
			def nested():
				pass

			return nested


		class Thing:
			@frappe.whitelist()
			def method(self):
				return 2


		@frappe.whitelist()
		def only_doc():
			\"\"\"Nothing but a docstring.\"\"\"


		def ctx():
			return {}
		""",
	"tasks.py": """\
		def daily():
			return 1


		def hourly():
			return 2
		""",
	"overrides.py": """\
		class Base:
			def inherited(self):
				return 0


		class DemoToDo(Base):
			def label(self):
				return "x"

			def _private(self):
				return "p"

			def __init__(self):
				self.x = 1

			def __repr__(self):
				return "r"
		""",
	"broken.py": """\
		import frappe


		@frappe.whitelist()
		def unreachable():
			return 0


		raise RuntimeError("broken on purpose")
		""",
	"jinja_methods.py": """\
		def shout(s):
			return s.upper()


		def _hidden():
			return None
		""",
	"tests/__init__.py": "",
	"tests/test_api.py": """\
		import frappe


		@frappe.whitelist()
		def not_a_target():
			return 0
		""",
	"patches/__init__.py": "",
	"patches/v1.py": """\
		import frappe


		@frappe.whitelist()
		def also_not():
			return 0
		""",
}


def write_package(root: Path) -> Path:
	pkg = root / "demo"
	for rel, text in PACKAGE.items():
		path = pkg / rel
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(textwrap.dedent(text))
	# The probe imports `frappe` names in these modules; a stand-in decorator is enough.
	(root / "frappe").mkdir()
	(root / "frappe" / "__init__.py").write_text(
		"def whitelist(*a, **k):\n\treturn a[0] if a and callable(a[0]) else (lambda f: f)\n"
	)
	return pkg


class TestHookTargets(unittest.TestCase):
	def test_kinds(self):
		got = testmap_probe.hook_targets(HOOKS, "demo")
		self.assertEqual(
			got,
			[
				("demo.tasks.daily", "T2"),
				("demo.tasks.hourly", "T2"),
				("demo.events.todo_on_update", "T3"),
				("demo.events.any_validate", "T3"),
				("demo.overrides.DemoToDo", "T4"),
				("demo.api.x", "T5"),
				("demo.perm.todo_query", "T5"),
				("demo.request.after", "T6"),
				("demo.install.after_install", "T6"),
				("demo.jinja_methods", "T6"),
				("demo.filters.upper", "T6"),
				("demo.timeline.content", "T6"),
				("demo.api.ctx", "T6"),
			],
		)

	def test_other_apps_are_not_targets(self):
		self.assertNotIn("frappe.other.validate", [p for p, _ in testmap_probe.hook_targets(HOOKS, "demo")])


class TestWhitelisted(unittest.TestCase):
	def test_scan(self):
		with tempfile.TemporaryDirectory() as tmp:
			pkg = write_package(Path(tmp))
			found = [path for path, _, _ in testmap_probe.whitelisted(pkg)]
		self.assertEqual(
			found,
			[
				"demo.api.ping",
				"demo.api.legacy",
				"demo.api.aliased",
				"demo.api.Thing.method",
				"demo.api.only_doc",
				"demo.broken.unreachable",
			],
		)


class TestProbe(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.root = Path(self.tmp.name)
		self.pkg = write_package(self.root)
		sys.path.insert(0, str(self.root))
		importlib.invalidate_caches()

	def tearDown(self):
		sys.path.remove(str(self.root))
		for name in [m for m in sys.modules if m == "demo" or m.startswith("demo.") or m == "frappe"]:
			del sys.modules[name]
		self.tmp.cleanup()

	def coverage(self, executed: dict[str, list[int]]) -> dict:
		files = {}
		for rel in ("demo/api.py", "demo/tasks.py", "demo/overrides.py", "demo/jinja_methods.py"):
			text = (self.root / rel).read_text().splitlines()
			lines = [
				n
				for n, line in enumerate(text, 1)
				if line.strip() and not line.lstrip().startswith(("#", "@", '"""'))
			]
			files[rel] = {
				"executed_lines": executed.get(rel, []),
				"missing_lines": [n for n in lines if n not in executed.get(rel, [])],
			}
		return {"files": files}

	def probe(self, executed, exempt):
		hooks = {
			"scheduler_events": {"daily": ["demo.tasks.daily"]},
			"extend_doctype_class": {"ToDo": ["demo.overrides.DemoToDo"]},
			"jinja": {"methods": ["demo.jinja_methods"]},
			"after_request": ["demo.missing.after"],
		}
		return testmap_probe.probe(hooks, "demo", self.pkg, self.coverage(executed), self.root, exempt)

	def test_verdicts(self):
		# api.py: line 8 is ping's return; tasks.py: line 2 is daily's.
		report = self.probe(
			{
				"demo/api.py": [8],
				"demo/tasks.py": [2],
				"demo/overrides.py": [8],
				"demo/jinja_methods.py": [2],
			},
			{"demo.api.legacy": "kept for old kiosks", "demo.api.gone": "no longer a target"},
		)
		by = {t["path"]: t for t in report["targets"]}
		self.assertTrue(by["demo.api.ping"]["tested"])
		self.assertEqual(by["demo.api.ping"]["file"], "demo/api.py")
		self.assertEqual(by["demo.api.ping"]["line"], 6)
		self.assertTrue(by["demo.api.legacy"]["exempt"])
		self.assertFalse(by["demo.api.aliased"]["tested"])
		self.assertTrue(by["demo.tasks.daily"]["tested"])
		self.assertEqual(by["demo.tasks.daily"]["kind"], "T2")
		# T4: the class's own public methods and __init__, not inherited or private ones.
		self.assertIn("demo.overrides.DemoToDo.label", by)
		self.assertIn("demo.overrides.DemoToDo.__init__", by)
		self.assertNotIn("demo.overrides.DemoToDo.inherited", by)
		self.assertNotIn("demo.overrides.DemoToDo._private", by)
		self.assertNotIn("demo.overrides.DemoToDo.__repr__", by)
		self.assertTrue(by["demo.overrides.DemoToDo.label"]["tested"])
		# A jinja `methods` entry naming a module: its public functions.
		self.assertTrue(by["demo.jinja_methods.shout"]["tested"])
		self.assertNotIn("demo.jinja_methods._hidden", by)
		# A module that raises on import: untested, with the error, and no crash.
		self.assertFalse(by["demo.broken.unreachable"]["tested"])
		self.assertIn("RuntimeError: broken on purpose", by["demo.broken.unreachable"]["error"])
		# A hook naming a module that does not exist.
		self.assertIn("missing.after", by["demo.missing.after"]["error"])
		self.assertEqual(by["demo.api.only_doc"]["error"], "no executable lines in its body")
		self.assertIn("demo.api.aliased", report["untested"])
		self.assertNotIn("demo.api.legacy", report["untested"])
		self.assertEqual(report["stale_exemptions"], ["demo.api.gone"])
		self.assertEqual(report["exempt"], 1)

	def test_a_tested_exemption_is_stale(self):
		report = self.probe({"demo/api.py": [8, 13]}, {"demo.api.legacy": "kept for old kiosks"})
		self.assertEqual(report["stale_exemptions"], ["demo.api.legacy"])

	def test_exemptions_from_pyproject(self):
		path = self.root / "pyproject.toml"
		path.write_text(
			'[[tool.ironclad.untested]]\ntarget = "demo.api.legacy"\nreason = "kept for old kiosks"\n'
		)
		self.assertEqual(testmap_probe.exemptions(path), {"demo.api.legacy": "kept for old kiosks"})
		self.assertEqual(testmap_probe.exemptions(self.root / "absent.toml"), {})


class TestBodySpan(unittest.TestCase):
	def test_decorated_multiline_signature_and_docstring(self):
		with tempfile.TemporaryDirectory() as tmp:
			f = Path(tmp) / "m.py"
			f.write_text(
				textwrap.dedent(
					"""\
					import functools


					@functools.cache
					@functools.cache
					def f(
						a,
						b,
					):
						\"\"\"Doc
						string.\"\"\"
						x = a
						return x + b
					"""
				)
			)
			self.assertEqual(testmap_probe.body_span(f, 4), (6, 12, 13))
			self.assertEqual(testmap_probe.body_span(f, 6), (6, 12, 13))
			self.assertIsNone(testmap_probe.body_span(f, 1))


class TestMarkdown(unittest.TestCase):
	REPORT: ClassVar[dict] = {
		"targets": [
			{
				"path": "demo.api.ping",
				"kind": "T1",
				"file": "demo/api.py",
				"line": 6,
				"tested": True,
				"exempt": False,
			},
			{
				"path": "demo.b|x",
				"kind": "T6",
				"file": None,
				"line": None,
				"tested": False,
				"exempt": False,
				"error": "boom",
			},
		],
		"untested": ["demo.b|x"],
		"exempt": 0,
		"stale_exemptions": ["demo.old"],
	}

	def test_markdown(self):
		md = testmap_markdown(self.REPORT)
		self.assertIn("**Untested**", md)
		self.assertIn("- `demo.b|x`", md)
		self.assertIn("**Stale exemptions**", md)
		self.assertIn("demo.b\\|x", md)
		self.assertIn("**no** (boom)", md)

	def test_failures(self):
		self.assertEqual(
			testmap_failures(self.REPORT),
			["untested: demo.b|x", "stale exemption: demo.old", "  demo.b|x: boom"],
		)

	def test_scripts_ship(self):
		for name in ("testmap_probe", "composition"):
			self.assertTrue(script(name).is_file())
			json.dumps(str(script(name)))


if __name__ == "__main__":
	unittest.main()
