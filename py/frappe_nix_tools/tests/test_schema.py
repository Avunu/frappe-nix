import unittest

from frappe_nix_tools.common import schema


class TestValidator(unittest.TestCase):
	def errors(self, value, node, app_only_allowed=True):
		return schema.Validator("profile.schema.json", "x", app_only_allowed=app_only_allowed).errors(
			value, node
		)

	def test_types_and_keywords(self):
		self.assertEqual(self.errors(1, {"type": "integer", "minimum": 0}), [])
		self.assertIn("expected integer", self.errors(True, {"type": "integer"})[0])
		self.assertEqual(self.errors(None, {"type": ["string", "null"]}), [])
		self.assertIn("must be one of", self.errors("c", {"enum": ["a", "b"]})[0])
		self.assertIn("does not match", self.errors("a\n", {"type": "string", "pattern": "^a$"})[0])
		self.assertIn("unique", self.errors([1, 1], {"type": "array", "uniqueItems": True})[0])
		self.assertEqual(self.errors("gates", {"anyOf": [{"const": "gates"}, {"type": "array"}]}), [])
		self.assertTrue(self.errors("x", {"anyOf": [{"const": "gates"}, {"type": "array"}]}))

	def test_objects(self):
		node = {
			"type": "object",
			"additionalProperties": False,
			"required": ["a"],
			"properties": {"a": {"type": "string"}},
		}
		self.assertEqual(self.errors({"a": "1"}, node), [])
		self.assertEqual(self.errors({}, node), ["x: a is required"])
		self.assertEqual(self.errors({"a": "1", "b": 2}, node), ["x.b: unknown key"])
		names = {
			"type": "object",
			"propertyNames": {"enum": ["npm"]},
			"additionalProperties": {"enum": ["daily"]},
		}
		self.assertEqual(self.errors({"npm": "daily"}, names), [])
		self.assertEqual(len(self.errors({"pip": "hourly"}, names)), 2)

	def test_refs_across_files(self):
		node = {"$ref": "profile.schema.json#/$defs/modules/editorconfig"}
		self.assertEqual(self.errors({"enable": True, "indent": "tab"}, node), [])
		self.assertIn("must be one of", self.errors({"indent": "tabs"}, node)[0])

	def test_app_only(self):
		node = {"$ref": "#/$defs/modules/typescript"}
		self.assertEqual(self.errors({"browser": False}, node), [])
		self.assertIn("app-only key", self.errors({"browser": False}, node, app_only_allowed=False)[0])

	def test_unknown_keyword_is_a_bug(self):
		with self.assertRaises(RuntimeError):
			self.errors(1, {"oneOf": []})

	def test_defaults_and_node_at(self):
		d = schema.defaults("tool-frappe-nix.schema.json")
		self.assertEqual(d["profile"], "minimal")
		self.assertEqual(d["siblings"], [])
		self.assertIs(d["typescript"]["browser"], True)
		self.assertEqual(d["tests"]["coverage"]["target"], 80)
		self.assertNotIn("frappe-major", d)
		self.assertIsNotNone(schema.node_at("tool-frappe-nix.schema.json", "js.oxlint.categories.perf"))
		self.assertIsNotNone(schema.node_at("tool-frappe-nix.schema.json", "known-apps.example/x.branch"))
		self.assertIsNone(schema.node_at("tool-frappe-nix.schema.json", "tests.nonsense"))


if __name__ == "__main__":
	unittest.main()
