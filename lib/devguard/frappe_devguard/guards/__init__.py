"""Guard registry.

Each guard module exposes ``NAME`` and ``install()``. ``install()`` registers
post-import hooks (or patches the standard library); it never imports Frappe
itself, so importing a guard on a machine with no bench is free.
"""

from . import (
	backups,
	egress,
	google,
	integrations,
	mail,
	mail_stdlib,
	objectstore,
	plaid,
	scheduler,
	webhooks,
)

#: Order matters only for readability — every guard is independent. ``egress``
#: first: it is the one that holds when the others have drifted.
GUARDS = (
	egress,
	mail_stdlib,
	mail,
	backups,
	objectstore,
	integrations,
	google,
	webhooks,
	plaid,
	scheduler,
)

__all__ = ["GUARDS"]
