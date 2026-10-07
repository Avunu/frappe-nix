import tomllib
import unittest

from frappe_nix_tools.common import config, data_path, schema


def load(stem):
	return tomllib.loads(data_path(f"profiles/{stem}.toml").read_text())


class TestBuiltinProfiles(unittest.TestCase):
	def test_names(self):
		self.assertEqual(config.builtin_names()[0], "minimal")
		self.assertIn("recommended@1.0", config.builtin_names())
		self.assertEqual(config.builtin_name("recommended"), config.builtin_names()[-1])

	def test_validate_against_the_profile_schema(self):
		for stem in config.builtin_names():
			with self.subTest(stem=stem):
				doc = load(stem)
				self.assertEqual(
					schema.Validator("profile.schema.json", stem, app_only_allowed=False).errors(doc), []
				)
				config.validate_profile(doc, stem, org=False)

	def test_every_module_has_a_table(self):
		for stem in config.builtin_names():
			doc = load(stem)
			with self.subTest(stem=stem):
				self.assertEqual([m for m in config.MODULES if m not in doc], [])
				self.assertTrue(all("enable" in doc[m] for m in config.MODULES))

	def test_the_schema_defines_exactly_the_modules(self):
		modules = schema.load("profile.schema.json")["$defs"]["modules"]
		self.assertEqual(list(modules), list(config.MODULES))
		self.assertEqual(len(config.MODULES), 29)

	def test_minimal_enables_only_the_dev_shell(self):
		doc = load("minimal")
		self.assertEqual([m for m in config.MODULES if doc[m]["enable"]], ["dev-shell"])
		self.assertEqual({k for m in config.MODULES for k in doc[m]}, {"enable"})

	def test_recommended_sets_no_organisation_value(self):
		org = load("recommended@1.0")["org"]
		self.assertEqual(
			{k for k, v in org.items() if v and not isinstance(v, dict)}, {"dev-docs-url", "repo-url"}
		)
		self.assertEqual(org["brand"]["tile-color"], "")

	def test_schema_defaults_are_the_recommended_values(self):
		# minimal sets no parameters: a module turned on over it gets the schema defaults,
		# which must be the recommended@1.0 values (§8.5).
		defaults = schema.defaults("profile.schema.json")
		recommended = load("recommended@1.0")

		def subset(default, value):
			if isinstance(value, dict) and value:
				return {k: subset(default[k], v) for k, v in value.items()}
			return default

		for m in config.MODULES:
			params = {k: v for k, v in recommended[m].items() if k != "enable"}
			with self.subTest(module=m):
				self.assertEqual({k: subset(defaults[m][k], v) for k, v in params.items()}, params)
		self.assertEqual(subset(defaults["org"], recommended["org"]), recommended["org"])


if __name__ == "__main__":
	unittest.main()
