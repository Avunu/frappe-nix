# Resolved by tests/ide.nix through the generated pyrightconfig.json: another
# app's module (an extraPaths entry) and a package in the dev virtualenv (.venv).
import iniconfig
from frappe.utils import fixture_marker

__all__ = ["fixture_marker", "iniconfig"]
