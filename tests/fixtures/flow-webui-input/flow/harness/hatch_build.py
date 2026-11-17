"""frappe's flow harness hook (flow/harness/hatch_build.py), as far as the build
here depends on it: an editable build packages nothing, and a source tree with a
PKG-INFO is a source distribution, which must already carry the web UI. The rest
of the real hook runs `npm ci` and `npm run build -w app`; here it fails, so a
build that got that far, and would have needed a registry, fails the check."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class WebUIBuildHook(BuildHookInterface):
	PLUGIN_NAME = "webui"

	def initialize(self, version, build_data):
		root = Path(self.root)
		webui_index = root / "src" / "flow_harness" / "server" / "webui" / "index.html"
		if version == "editable":
			return
		if (root / "PKG-INFO").is_file():
			if not webui_index.is_file():
				raise RuntimeError(f"source distribution does not contain {webui_index}")
			return
		raise RuntimeError("this build would have run npm to package the web UI")
