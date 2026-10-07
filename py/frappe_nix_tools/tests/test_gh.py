import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from frappe_nix_tools.common import gh
from frappe_nix_tools.common.report import EnvError

# A stand-in `gh`: records its argv and stdin, and answers by endpoint. The shebang is this
# interpreter, since a build sandbox has no /usr/bin/env.
FAKE_GH = (
	f"#!{sys.executable}\n"
	+ """import json, os, sys
log = os.environ["FAKE_GH_LOG"]
with open(log, "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "stdin": sys.stdin.read() if "--input" in sys.argv else None}) + "\\n")
endpoint = sys.argv[4]
if endpoint == "fail":
    print("HTTP 404: Not Found", file=sys.stderr)
    sys.exit(1)
if endpoint == "empty":
    sys.exit(0)
if "--paginate" in sys.argv:
    print(json.dumps([[1, 2], [3]]))
else:
    print(json.dumps({"endpoint": endpoint}))
"""
)


class TestGh(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		bin_dir = Path(self.tmp.name)
		(bin_dir / "gh").write_text(FAKE_GH)
		(bin_dir / "gh").chmod(0o755)
		self.log = bin_dir / "log.jsonl"
		env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_LOG": str(self.log)}
		self.patch = mock.patch.dict(os.environ, env)
		self.patch.start()

	def tearDown(self):
		self.patch.stop()
		self.tmp.cleanup()

	def calls(self):
		return [json.loads(line) for line in self.log.read_text().splitlines()]

	def test_get(self):
		self.assertEqual(gh.api("repos/a/b"), {"endpoint": "repos/a/b"})
		self.assertEqual(self.calls()[0]["argv"], ["api", "--method", "GET", "repos/a/b"])

	def test_fields_and_body(self):
		gh.api("repos/a/b/rulesets", method="POST", fields={"x": "1"}, body={"name": "develop"})
		call = self.calls()[0]
		self.assertEqual(call["argv"][:4], ["api", "--method", "POST", "repos/a/b/rulesets"])
		self.assertIn("x=1", call["argv"])
		self.assertEqual(json.loads(call["stdin"]), {"name": "develop"})

	def test_paginate_flattens(self):
		self.assertEqual(gh.api("repos/a/b/pulls", paginate=True), [1, 2, 3])

	def test_empty_and_failure(self):
		self.assertIsNone(gh.api("empty"))
		with self.assertRaisesRegex(EnvError, "404"):
			gh.api("fail")


if __name__ == "__main__":
	unittest.main()
