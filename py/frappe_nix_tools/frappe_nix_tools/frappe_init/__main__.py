"""``python -m frappe_nix_tools.frappe_init``: how lib/init.nix runs frappe-init."""

from frappe_nix_tools.frappe_init.command import entry

entry()
