"""frappe-nix's side of the devguard contract.

The rest of this package's core (``__init__``, ``_hook``, ``_settings``,
``_patch``, ``guards/egress`` and ``guards/mail_stdlib``) is python-devops-nix's
(``python/devguard``), merged in by ``lib.devguard.mkPackage`` together with
the ``_baked.json`` it writes from the ``frappe-nix.devguard`` options.
python-devops-nix's docs/conventions.md, "The core and the product's package", has
the contract this module fills in.

Development only: grafted into the development virtualenv, never into
``prodPythonEnv`` (``checks.devguard-production`` asserts it).
"""

#: The settings variables are ``FRAPPE_DEVGUARD_*``.
ENV_PREFIX = "FRAPPE"

#: What a fail-closed ``require`` names.
FRAMEWORK = "Frappe"
PRODUCT = "frappe-nix"

#: Merged over the core's defaults, key by key: the settings of frappe-nix's
#: own guards, and the mail guard's Frappe-layer keys.
DEFAULTS = {
	"guards": {
		"mail": {
			"sender": "notifications@example.com",
			"unmute": True,
		},
		"backups": {"enable": True},
		"objectstore": {"conditional_writes": True, "enable": True, "mode": "local"},
		"integrations": {"enable": True, "allow_hosts": []},
		"google": {"enable": True},
		"webhooks": {"enable": True},
		"plaid": {"enable": True},
		# Enforced by guards/scheduler.py, which logs as "scheduler".
		"jobs": {
			"enable": True,
			"block_server_scripts": True,
			# Matched EXACT-STRING against Scheduled Job Type.method. Never
			# substring: hourly_maintenance also runs
			# frappe.desk.page.backups.backups.delete_downloadable_backups,
			# a purely local retention reaper, and matching "backup" loosely
			# would disable it and fill the disk.
			# Both layouts, because exact-matching means a moved module is not
			# merely unguarded, it is unguarded *silently*: Frappe 16 lifted
			# these three integrations out of core into the standalone
			# `frappe/offsite_backups` app, whose hooks.py registers the same
			# seven jobs under its own dotted paths. See guards/backups.py.
			"blocked_jobs": [
				# <= v15, frappe core
				"frappe.integrations.doctype.dropbox_settings.dropbox_settings.take_backups_daily",
				"frappe.integrations.doctype.dropbox_settings.dropbox_settings.take_backups_weekly",
				"frappe.integrations.doctype.s3_backup_settings.s3_backup_settings.take_backups_daily",
				"frappe.integrations.doctype.s3_backup_settings.s3_backup_settings.take_backups_weekly",
				"frappe.integrations.doctype.s3_backup_settings.s3_backup_settings.take_backups_monthly",
				"frappe.integrations.doctype.google_drive.google_drive.daily_backup",
				"frappe.integrations.doctype.google_drive.google_drive.weekly_backup",
				# >= v16, the standalone app
				"offsite_backups.offsite_backups.doctype.dropbox_settings.dropbox_settings.take_backups_daily",
				"offsite_backups.offsite_backups.doctype.dropbox_settings.dropbox_settings.take_backups_weekly",
				"offsite_backups.offsite_backups.doctype.s3_backup_settings.s3_backup_settings.take_backups_daily",
				"offsite_backups.offsite_backups.doctype.s3_backup_settings.s3_backup_settings.take_backups_weekly",
				"offsite_backups.offsite_backups.doctype.s3_backup_settings.s3_backup_settings.take_backups_monthly",
				"offsite_backups.offsite_backups.doctype.google_drive.google_drive.daily_backup",
				"offsite_backups.offsite_backups.doctype.google_drive.google_drive.weekly_backup",
			],
		},
	},
}

#: frappe-nix's guards, under guards/, installed after the core's egress and
#: mail_stdlib. Order matters only for readability: every guard is independent.
GUARDS = (
	"mail",
	"backups",
	"objectstore",
	"integrations",
	"google",
	"webhooks",
	"plaid",
	"scheduler",
)

#: integrations.allowHosts opens a host to egress too, so one list serves both.
EGRESS_ALLOW = (("integrations", "allow_hosts"),)


def log(guard, message):
	"""Frappe's logger, beside stderr.

	Raising is not enough on the scheduler path: Frappe's backup integrations
	catch Exception and turn it into a "backup failed" email,
	ScheduledJobType.execute catches it and records status "Failed", and
	Dropbox's upload_from_folder swallows per-file exceptions into a list. The
	core swallows anything this raises.
	"""
	import frappe

	frappe.logger("frappe_devguard").warning(f"[{guard}] {message}")


def user_error(guard, message):
	"""Surface a refusal in the desk UI."""
	import frappe

	frappe.throw(message, title="Blocked by frappe-devguard")
