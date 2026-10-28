"""Tests for frappe-nix's side of frappe_devguard, as a bench's virtualenv
carries it.

Run: python3 -I test_devguard.py <site-packages>

<site-packages> is a virtualenv's, after python-devops-nix's
lib.python.withGrafts put frappe_devguard in it: the devguard core
(python/devguard) merged with lib/devguard/frappe_devguard by
lib.devguard.mkPackage, with settings baked in. ``site.addsitedir`` runs its
.pth file the way interpreter start-up does, so the guards are installed
exactly as in a bench. ``checks.devguard`` runs this on every interpreter
lib/frappe-presets.json names.

The core (the post-import hook, settings resolution, the patch helpers, the
egress and mail transport guards) is python-devops-nix's and tested there,
against a stub product. What is tested here is frappe-nix's: the names a bench
relies on (the .pth file, the log prefix), _product.py, _frappe.py and the
Frappe guards, with stub modules standing in for Frappe and botocore. No
Frappe, no site and no network.
"""

import contextlib
import io
import os
import site
import socket
import sys
import types

SITE = os.path.abspath(sys.argv[1])

# Keep a developer's own settings, and their shell's runtime file, out of it.
for _key in [k for k in os.environ if k.startswith("FRAPPE_DEVGUARD_")]:
	del os.environ[_key]
os.environ.pop("DEVENV_RUNTIME", None)

FAILURES = []


def check(label, condition, detail=""):
	if condition:
		print(f"ok   {label}")
	else:
		print(f"FAIL {label} {detail}")
		FAILURES.append(label)


def expect_raises(label, exc_type, fn):
	try:
		fn()
	except exc_type:
		print(f"ok   {label}")
	except Exception as exc:  # the point is to report the mismatch
		print(f"FAIL {label} raised {type(exc).__name__}: {exc}")
		FAILURES.append(label)
	else:
		print(f"FAIL {label} did not raise")
		FAILURES.append(label)


def stderr_of(fn):
	"""What FN wrote to stderr, and the exception it raised (or None)."""
	buffer = io.StringIO()
	raised = None
	with contextlib.redirect_stderr(buffer):
		try:
			fn()
		except Exception as exc:  # recorded for the caller
			raised = exc
	return buffer.getvalue(), raised


# --------------------------------------------------------------------------
# the graft: what a bench's virtualenv carries
# --------------------------------------------------------------------------

pths = sorted(name for name in os.listdir(SITE) if name.endswith(".pth"))
check("the .pth file is zzz-frappe-devguard.pth", pths == ["zzz-frappe-devguard.pth"], pths)
with open(os.path.join(SITE, "zzz-frappe-devguard.pth")) as _handle:
	_line = _handle.read()
check(
	"and it imports and installs frappe_devguard",
	_line == "import frappe_devguard; frappe_devguard.install()\n",
	repr(_line),
)

site.addsitedir(SITE)  # runs the .pth: exactly what interpreter start-up does

import frappe_devguard
from frappe_devguard import DevGuardBlocked, DevGuardPatchError, _frappe, _patch, _product
from frappe_devguard._frappe import disarm_subclasses, redirect_kwargs
from frappe_devguard._patch import require

check(
	"frappe_devguard is imported from the graft",
	os.path.dirname(os.path.dirname(frappe_devguard.__file__)) == SITE,
	frappe_devguard.__file__,
)
check(
	"its settings are FRAPPE_DEVGUARD_*",
	frappe_devguard._settings.env_name("mail", "port") == "FRAPPE_DEVGUARD_MAIL_PORT",
)
check(
	"the guards installed are the core's then frappe-nix's",
	[guard.NAME for guard in frappe_devguard.all_guards()]
	== [
		"egress",
		"mail",
		"mail",
		"backups",
		"objectstore",
		"integrations",
		"google",
		"webhooks",
		"plaid",
		"jobs",
	],
	[guard.NAME for guard in frappe_devguard.all_guards()],
)
check(
	"the transport guards are patched at start-up",
	frappe_devguard.status().get("socket.socket.connect") == "patched",
	frappe_devguard.status(),
)

# -- the baked settings (lib.devguard.mkPackage's _baked.json) --

st = frappe_devguard.settings()
check("baked settings reach the guards", st.mail_port == 2525, st.mail_port)
check(
	"the baked job denylist adds to frappe-nix's",
	"baked.tasks.push" in st.blocked_jobs
	and "frappe.integrations.doctype.google_drive.google_drive.daily_backup" in st.blocked_jobs,
	st.blocked_jobs,
)
check("frappe-nix's mail defaults are merged in", st.text("mail", "sender") == "notifications@example.com")
check("objectstore defaults to local mode", st.text("objectstore", "mode") == "local")

# -- the log lines: frappe_devguard[<guard>], unchanged by the move to the core --

BLOCKED = ("192.0.2.1", 9)  # TEST-NET-1: nothing answers there


def connect(address):
	sock = socket.socket()
	sock.settimeout(0.3)
	try:
		return sock.connect(address)
	finally:
		sock.close()


_log, _raised = stderr_of(lambda: connect(BLOCKED))
check("a refusal raises DevGuardBlocked", isinstance(_raised, DevGuardBlocked), repr(_raised))
check(
	"whose message is frappe_devguard[egress]: …",
	str(_raised).startswith("frappe_devguard[egress]: blocked connection to 192.0.2.1:9"),
	str(_raised),
)
check(
	"the banner is WARNING frappe_devguard[egress] ACTIVE -- …",
	"WARNING frappe_devguard[egress] ACTIVE -- outbound connections from this process are refused" in _log,
	_log,
)
check(
	"and it names frappe-nix's option and variables",
	"devguard.egress.allowHosts" in _log
	and "FRAPPE_DEVGUARD_EGRESS_ALLOW_HOSTS=host[,host...]" in _log
	and "FRAPPE_DEVGUARD_DISABLE=egress" in _log,
	_log,
)
check(
	"the refusal is logged as WARNING frappe_devguard[egress] …",
	"WARNING frappe_devguard[egress] blocked connection to 192.0.2.1:9" in _log,
	_log,
)

_exc = None
try:
	require(types.SimpleNamespace(), "no_such_target")
except DevGuardPatchError as exc:
	_exc = exc
check(
	"a moved target names Frappe and frappe-nix",
	_exc is not None
	and str(_exc).startswith("frappe_devguard: ")
	and "Frappe's internals have moved" in str(_exc)
	and "Update frappe-nix, or set FRAPPE_DEVGUARD_ENABLED=0" in str(_exc),
	str(_exc),
)


# --------------------------------------------------------------------------
# one list for egress and integrations (_product.EGRESS_ALLOW)
# --------------------------------------------------------------------------

from frappe_devguard.guards import egress, integrations

os.environ["FRAPPE_DEVGUARD_INTEGRATIONS_ALLOW_HOSTS"] = "api.sandbox.test"
check("integrations.allowHosts opens a host to egress", egress.listed("api.sandbox.test"))
os.environ.pop("FRAPPE_DEVGUARD_INTEGRATIONS_ALLOW_HOSTS")
check("and dropping it closes it again", not egress.listed("api.sandbox.test"))

os.environ["FRAPPE_DEVGUARD_EGRESS_ALLOW_HOSTS"] = "*.stripe.test"
check(
	"egress.allowHosts opens a host to integrations",
	integrations.allowed("https://api.stripe.test/v1/charges"),
)
check("an unlisted integration host is refused", not integrations.allowed("https://api.other.test/"))
os.environ.pop("FRAPPE_DEVGUARD_EGRESS_ALLOW_HOSTS")
check("integrations: loopback is allowed", integrations.allowed("http://127.0.0.1:8000/api"))
check("integrations: local names are allowed", integrations.allowed("http://site.localhost:8000/"))


# --------------------------------------------------------------------------
# _product.py's hooks into Frappe: the logger, and frappe.throw
# --------------------------------------------------------------------------


class StubThrow(Exception):
	pass


LOGGED = []
_frappe_stub = types.ModuleType("frappe")
_frappe_stub.logger = lambda name: types.SimpleNamespace(
	warning=lambda message: LOGGED.append((name, message))
)


def _stub_throw(message, title=None):
	raise StubThrow(f"{title}: {message}")


_frappe_stub.throw = _stub_throw
sys.modules["frappe"] = _frappe_stub

_log, _ = stderr_of(lambda: _patch.warn("backups", "hello"))
check(
	"warn writes WARNING frappe_devguard[backups] …", _log == "WARNING frappe_devguard[backups] hello\n", _log
)
check(
	"and to Frappe's logger",
	LOGGED == [("frappe_devguard", "[backups] hello")],
	LOGGED,
)

_log, _raised = stderr_of(lambda: _frappe.throw("backups", "upload refused"))
check(
	"throw surfaces the refusal through frappe.throw",
	isinstance(_raised, StubThrow) and str(_raised) == "Blocked by frappe-devguard: upload refused",
	repr(_raised),
)
check("after logging it", "WARNING frappe_devguard[backups] upload refused" in _log, _log)
_raised = stderr_of(_frappe.throwing("backups", "Dropbox backup", "refused", banner="no uploads"))[1]
check(
	"throwing names what it replaced",
	isinstance(_raised, StubThrow) and "Dropbox backup: refused" in str(_raised),
	repr(_raised),
)

_frappe_stub.logger = None  # a logger that fails must not fail the guard
_log, _raised = stderr_of(lambda: _patch.warn("backups", "still here"))
check("a failing logger is swallowed", _raised is None and "still here" in _log, repr(_raised))
del sys.modules["frappe"]


# --------------------------------------------------------------------------
# subclass disarming (the mechanism that defeats override_doctype_class)
# --------------------------------------------------------------------------


class Base:
	def send(self):
		return "base"


class EarlySubclass(Base):
	def send(self):
		return "escaped"


disarm_subclasses(Base, ("send",))
check("existing subclass is disarmed", EarlySubclass().send() == "base")


class LateSubclass(Base):
	"""Stands in for a controller imported later by get_controller."""

	def send(self):
		return "escaped"


check("late subclass is disarmed", LateSubclass().send() == "base")


class Untouched(Base):
	def other(self):
		return "kept"


check("unrelated attributes survive", Untouched().other() == "kept")


def _sample(self, server, port=None, password=None):
	return (server, port, password)


args, kwargs = redirect_kwargs(
	_sample, None, ("real.example.com",), {"password": "hunter2"}, {"server": "127.0.0.1", "port": 1025}
)
check(
	"call arguments are rewritten before delegation",
	_sample(*args, **kwargs) == ("127.0.0.1", 1025, "hunter2"),
	_sample(*args, **kwargs),
)

expect_raises(
	"a missing patch target fails loudly",
	DevGuardPatchError,
	lambda: require(Base, "no_such_method"),
)


# --------------------------------------------------------------------------
# the synthesised account used on sites with no Email Account at all
# --------------------------------------------------------------------------

from frappe_devguard.guards.mail import _synthetic_account


class FakeEmailAccount:
	@classmethod
	def from_record(cls, record):
		account = cls()
		account.record = record
		return account


record = _synthetic_account(FakeEmailAccount).record
check("synthetic account targets the catcher", record["smtp_server"] == "127.0.0.1")
check("synthetic account uses the catcher port", record["smtp_port"] == 2525)
check("synthetic account carries no password", "password" not in record)
check(
	"synthetic account cannot negotiate TLS",
	record["use_tls"] == 0 and record["use_ssl_for_outgoing"] == 0,
)
check(
	"synthetic account never touches a mailbox",
	record["enable_incoming"] == 0 and record["append_emails_to_sent_folder"] == 0,
)
check(
	"synthetic account preserves the real sender",
	record["always_use_account_email_id_as_sender"] == 0,
)


# --------------------------------------------------------------------------
# whitelist swap — a replacement the framework still recognises
# --------------------------------------------------------------------------

import types


def stub_frappe(container=list):
	"""Minimal stand-in for the four registries frappe.whitelist() populates.

	``container`` is ``list`` on Frappe v15 and ``set`` from v16 on. The swap
	has to hold for both, so every assertion below runs twice.
	"""
	stub = types.ModuleType("frappe")
	stub.whitelisted = container()
	stub.guest_methods = container()
	stub.xss_safe_methods = container()
	stub.allowed_http_methods_for_whitelisted_func = {}
	stub.logger = lambda *_a, **_k: types.SimpleNamespace(warning=lambda *_a, **_k: None)
	sys.modules["frappe"] = stub
	return stub


frappe_stub = stub_frappe()
from frappe_devguard._frappe import assert_not_overridden, rewhitelist


def register(registry, fn):
	(registry.append if isinstance(registry, list) else registry.add)(fn)


for container in (list, set):
	kind = container.__name__
	frappe_stub = stub_frappe(container)

	def take_backup():
		return "uploaded"

	register(frappe_stub.whitelisted, take_backup)
	register(frappe_stub.guest_methods, take_backup)
	frappe_stub.allowed_http_methods_for_whitelisted_func[take_backup] = ["GET", "POST"]

	def refuse():
		return "blocked"

	replacement = rewhitelist(take_backup, refuse)

	check(f"[{kind}] replacement is whitelisted", replacement in frappe_stub.whitelisted)
	check(f"[{kind}] original is no longer whitelisted", take_backup not in frappe_stub.whitelisted)
	check(
		f"[{kind}] whitelist did not grow",
		len(frappe_stub.whitelisted) == 1,
		frappe_stub.whitelisted,
	)
	check(
		f"[{kind}] http methods follow the replacement",
		frappe_stub.allowed_http_methods_for_whitelisted_func.get(replacement) == ["GET", "POST"],
	)
	check(
		f"[{kind}] stale http-methods key is gone",
		take_backup not in frappe_stub.allowed_http_methods_for_whitelisted_func,
	)
	check(f"[{kind}] guest_methods follows too", replacement in frappe_stub.guest_methods)
	# The guest registries take no fallback registration: the original was never
	# xss_safe, and adding the replacement there would widen access, not keep it.
	check(
		f"[{kind}] xss_safe_methods stays empty",
		not frappe_stub.xss_safe_methods,
		frappe_stub.xss_safe_methods,
	)
	check(
		f"[{kind}] replacement keeps the original identity",
		replacement.__name__ == "take_backup" and replacement.__module__ == take_backup.__module__,
	)

frappe_stub.get_hooks = lambda _name: {"some.other.cmd": ["app.override"]}
assert_not_overridden("backups", ["protected.cmd"])  # must not raise
print("ok   assert_not_overridden tolerates an unrelated override map")


# --------------------------------------------------------------------------
# scheduler denylist — exact match only
#
# Its settings are the shared jobs tree: jobs.blocked_jobs (frappe-nix's
# built-in list, in _product.py) and jobs.extra_blocked (devguard.jobs.extraBlocked).
# --------------------------------------------------------------------------

from frappe_devguard.guards import scheduler
from frappe_devguard.guards.scheduler import blocked_jobs

jobs = set(_product.DEFAULTS["guards"]["jobs"]["blocked_jobs"])
check(
	"blocked_jobs is the built-in list and the baked additions", blocked_jobs() == jobs | {"baked.tasks.push"}
)
check(
	"core backup jobs are blocked",
	"frappe.integrations.doctype.s3_backup_settings.s3_backup_settings.take_backups_daily" in jobs,
)
# Frappe 16 moved these three integrations into the standalone
# `frappe/offsite_backups` app, which re-registers the same seven jobs under
# its own dotted paths. blocked_jobs is exact-match, so the old spellings alone
# would leave a bench with that app installed pushing to production nightly.
check(
	"the standalone app's jobs are blocked too",
	"offsite_backups.offsite_backups.doctype.s3_backup_settings.s3_backup_settings.take_backups_daily"
	in jobs,
)
check(
	"including google drive's, which is spelled differently",
	"offsite_backups.offsite_backups.doctype.google_drive.google_drive.daily_backup" in jobs,
)
check(
	"the local retention reaper is NOT blocked",
	"frappe.desk.page.backups.backups.delete_downloadable_backups" not in jobs,
)
check("seven entries per layout are listed", len(jobs) == 14, len(jobs))

os.environ["FRAPPE_DEVGUARD_JOBS_EXTRA_BLOCKED"] = "myapp.tasks.push_backup"
check(
	"extra jobs are unioned in, not replacing",
	blocked_jobs() == jobs | {"myapp.tasks.push_backup"},
	len(blocked_jobs()),
)
os.environ.pop("FRAPPE_DEVGUARD_JOBS_EXTRA_BLOCKED")

check("the guard's settings key is jobs", scheduler.NAME == "jobs")

# The pre-rename variables, carried over with a warning through 1.x, are not
# read since frappe-nix 2.0.0.
os.environ["FRAPPE_DEVGUARD_SCHEDULER_EXTRA_BLOCKED_JOBS"] = "myapp.tasks.legacy"
check(
	"FRAPPE_DEVGUARD_SCHEDULER_EXTRA_BLOCKED_JOBS adds nothing",
	"myapp.tasks.legacy" not in blocked_jobs(),
)
os.environ.pop("FRAPPE_DEVGUARD_SCHEDULER_EXTRA_BLOCKED_JOBS")
check("nothing is left to adopt them", not hasattr(scheduler, "_adopt_renamed"))


# What a skipped job logs: frappe_devguard[scheduler], the framework's word.
class FakeJob:
	method = "frappe.integrations.doctype.google_drive.google_drive.daily_backup"
	ran = False

	def get(self, key, default=None):
		return default

	def db_set(self, *_args, **_kwargs):
		pass


_sjt = types.ModuleType("frappe.core.doctype.scheduled_job_type.scheduled_job_type")


class ScheduledJobType:
	def execute(self):
		self.ran = True


_sjt.ScheduledJobType = ScheduledJobType
scheduler._patch_scheduled_job_type(_sjt)
_frappe_stub = types.ModuleType("frappe")
_frappe_stub.db = types.SimpleNamespace(commit=lambda: None)
_frappe_utils = types.ModuleType("frappe.utils")
_frappe_utils.now_datetime = lambda: None
sys.modules["frappe"], sys.modules["frappe.utils"] = _frappe_stub, _frappe_utils
_job = FakeJob()
_log, _raised = stderr_of(lambda: ScheduledJobType.execute(_job))
check("a denylisted job is skipped", _raised is None and not _job.ran, repr(_raised))
check(
	"and logged as WARNING frappe_devguard[scheduler] …",
	"WARNING frappe_devguard[scheduler] ACTIVE -- scheduled jobs that reach production services are skipped"
	in _log
	and f"WARNING frappe_devguard[scheduler] skipping scheduled job {FakeJob.method!r}" in _log,
	_log,
)
os.environ["FRAPPE_DEVGUARD_DISABLE"] = "jobs"
ScheduledJobType.execute(_job)
check("FRAPPE_DEVGUARD_DISABLE=jobs lets it run", _job.ran)
os.environ.pop("FRAPPE_DEVGUARD_DISABLE")
del sys.modules["frappe.utils"]

# override_whitelisted_methods is consulted before get_attr, so the cmd strings
# assert_not_overridden watches have to name both layouts as well.
from frappe_devguard.guards.backups import _BACKUP_PREFIXES, _PROTECTED_CMDS

check(
	"both backup layouts are guarded",
	_BACKUP_PREFIXES == ("frappe.integrations", "offsite_backups.offsite_backups"),
	_BACKUP_PREFIXES,
)
check(
	"protected endpoints cover core",
	"frappe.integrations.doctype.s3_backup_settings.s3_backup_settings.take_backups_s3" in _PROTECTED_CMDS,
)
check(
	"protected endpoints cover the standalone app",
	"offsite_backups.offsite_backups.doctype.s3_backup_settings.s3_backup_settings.take_backups_s3"
	in _PROTECTED_CMDS,
)
check("four endpoints per layout", len(_PROTECTED_CMDS) == 8, len(_PROTECTED_CMDS))

# --------------------------------------------------------------------------
# `bench migrate-to`: the Frappe Cloud site migrator
#
# frappe.integrations.frappe_providers is imported on every boot (frappe.boot
# reaches cloud_settings through it). Before 16.53 it re-exported the migrator
# from an eager import; 16.53 imports it inside migrate_to, so the package has
# no frappecloud_migrator at all, and requiring one failed every boot.
# --------------------------------------------------------------------------

from frappe_devguard.guards.backups import _rebind_frappe_providers


def frappe_providers_module(*, reexport):
	module = types.ModuleType("frappe.integrations.frappe_providers")

	def frappecloud_migrator():
		raise AssertionError("the real migrator must not run")

	module.migrate_to = frappecloud_migrator
	if reexport:
		module.frappecloud_migrator = frappecloud_migrator
	return module


_lazy = frappe_providers_module(reexport=False)
_rebind_frappe_providers(_lazy)  # must not raise
expect_raises("16.53's lazy import: migrate_to is blocked", DevGuardBlocked, _lazy.migrate_to)
check("…and no re-export is invented", not hasattr(_lazy, "frappecloud_migrator"))

_eager = frappe_providers_module(reexport=True)
_rebind_frappe_providers(_eager)
expect_raises("the eager re-export: migrate_to is blocked", DevGuardBlocked, _eager.migrate_to)
expect_raises("…and so is the re-export", DevGuardBlocked, _eager.frappecloud_migrator)

expect_raises(
	"migrate_to gone fails loudly",
	DevGuardPatchError,
	lambda: _rebind_frappe_providers(types.ModuleType("frappe.integrations.frappe_providers")),
)

# --------------------------------------------------------------------------
# the Email Queue outgoing-transport patch
#
# `bench new-site` imports this module while installing the Email Queue DocType
# (on_doctype_update -> load_doctype_module), which is the first thing in a
# bench's life that reliably reaches this guard - post-install, nothing imports
# it until mail is actually sent. Frappe 16 renamed fetch_smtp_server to
# fetch_outgoing_server and added "Frappe Mail" beside SMTP, an HTTP transport
# to a remote site that smtplib never sees.
# --------------------------------------------------------------------------

from frappe_devguard.guards.mail import _patch_email_queue


class FakeSMTPServer:
	def __init__(self, server=None, port=None, **_kwargs):
		self.server = server
		self.port = port


class FakeAccount:
	"""Enough of a Document: ``.get()`` reads the attribute."""

	def __init__(self, service=""):
		self.service = service

	def get(self, key, default=None):
		return getattr(self, key, default)


class FakeQueueDoc:
	def __init__(self, account):
		self.account = account

	def get_email_account(self, raise_error=False):
		return self.account


def email_queue_module(fetch_name):
	module = types.ModuleType("frappe.email.doctype.email_queue.email_queue")

	class SendMailContext:
		def __init__(self, account):
			self.queue_doc = FakeQueueDoc(account)
			self.smtp_server = None
			self.frappe_mail_client = "a real Frappe Mail client"
			self.email_account_doc = None

	def original(self):
		raise AssertionError("the original transport lookup must not run")

	if fetch_name:
		setattr(SendMailContext, fetch_name, original)
	module.SendMailContext = SendMailContext
	module.SMTPServer = FakeSMTPServer
	module.get_hook_method = lambda _name, fallback=None: fallback
	return module


check("mail guard still enabled here", frappe_devguard.settings().guard_enabled("mail"))

_st = frappe_devguard.settings()

_mod16 = email_queue_module("fetch_outgoing_server")
_patch_email_queue(_mod16)
_ctx = _mod16.SendMailContext(FakeAccount(service="Frappe Mail"))
_ctx.fetch_outgoing_server()

check("frappe 16's outgoing-server lookup is patched", _ctx.smtp_server is not None)
check("the queue is pointed at the catcher", _ctx.smtp_server.server == _st.mail_host)
check("on the catcher's port", _ctx.smtp_server.port == _st.mail_port)
check(
	"the Frappe Mail HTTP transport is disarmed",
	_ctx.email_account_doc.service == "",
	_ctx.email_account_doc.service,
)
check("and its client is dropped", _ctx.frappe_mail_client is None)

_mod15 = email_queue_module("fetch_smtp_server")
_patch_email_queue(_mod15)
_ctx15 = _mod15.SendMailContext(FakeAccount())
_ctx15.fetch_smtp_server()
check("the pre-16 spelling is still patched", _ctx15.smtp_server.server == _st.mail_host)

expect_raises(
	"neither spelling surviving fails loudly",
	DevGuardPatchError,
	lambda: _patch_email_queue(email_queue_module(None)),
)


# --------------------------------------------------------------------------
# the object store: additive-only S3
#
# Stand-ins for botocore.client.BaseClient and botocore.signers, which are all
# the guard touches. `calls` records what would have reached the network.
# --------------------------------------------------------------------------

from frappe_devguard.guards import objectstore


class FakeClientError(Exception):
	def __init__(self, code):
		super().__init__(code)
		self.response = {"Error": {"Code": code}}


def fake_botocore():
	client_mod = types.ModuleType("botocore.client")
	signers_mod = types.ModuleType("botocore.signers")

	class BaseClient:
		def __init__(self, endpoint="https://s3.example.com", service="s3", existing=(), head_error=None):
			self.meta = types.SimpleNamespace(
				endpoint_url=endpoint,
				service_model=types.SimpleNamespace(service_name=service),
			)
			self.existing = set(existing)
			self.head_error = head_error
			self.calls = []
			# Per call: would the egress guard have let its connection through?
			self.passage = []

		def _make_api_call(self, operation_name, api_params):
			self.calls.append((operation_name, dict(api_params)))
			self.passage.append(egress.in_passage())
			if operation_name == "HeadObject":
				if self.head_error:
					raise FakeClientError(self.head_error)
				if api_params["Key"] not in self.existing:
					raise FakeClientError("404")
				return {"ContentLength": 1}
			return {"ok": operation_name}

	def generate_presigned_url(self, ClientMethod, Params=None, **_kwargs):
		return f"https://signed/{ClientMethod}"

	def generate_presigned_post(self, Bucket, Key, **_kwargs):
		return {"url": "https://signed/post"}

	client_mod.BaseClient = BaseClient
	signers_mod.generate_presigned_url = generate_presigned_url
	signers_mod.generate_presigned_post = generate_presigned_post
	objectstore._patch_client(client_mod)
	objectstore._patch_signers(signers_mod)
	return BaseClient, signers_mod


S3, _signers = fake_botocore()


def ops(client):
	return [name for name, _params in client.calls]


def put(client, key="new.pdf"):
	return client._make_api_call("PutObject", {"Bucket": "prod", "Key": key, "Body": b"x"})


os.environ.pop("FRAPPE_DEVGUARD_OBJECTSTORE_MODE", None)
check("objectstore defaults to local mode", objectstore.mode() == "local")

# -- local mode (the default) --

_c = S3()
_c._make_api_call("GetObject", {"Bucket": "prod", "Key": "a"})
check("reads reach the store", ops(_c) == ["GetObject"], ops(_c))
check(
	"but local mode gives them no egress passage: the bucket stays unreached",
	_c.passage == [False],
	_c.passage,
)

_c = S3()
_resp = _c._make_api_call("DeleteObject", {"Bucket": "prod", "Key": "a"})
check("DeleteObject never reaches the store", ops(_c) == [], ops(_c))
check("and reports success, as S3 does", _resp["ResponseMetadata"]["HTTPStatusCode"] == 204)

_c = S3()
_resp = _c._make_api_call(
	"DeleteObjects", {"Bucket": "prod", "Delete": {"Objects": [{"Key": "a"}, {"Key": "b"}]}}
)
check("DeleteObjects never reaches the store", ops(_c) == [], ops(_c))
check(
	"and reports every key as not deleted",
	[e["Key"] for e in _resp["Errors"]] == ["a", "b"] and not _resp.get("Deleted"),
	_resp,
)

_c = S3()
expect_raises("local mode refuses writes", DevGuardBlocked, lambda: put(_c))
check("before anything is sent", ops(_c) == [], ops(_c))

expect_raises(
	"bucket configuration is refused",
	DevGuardBlocked,
	lambda: S3()._make_api_call(
		"PutBucketLifecycleConfiguration", {"Bucket": "prod", "LifecycleConfiguration": {}}
	),
)

# -- push mode --

os.environ["FRAPPE_DEVGUARD_OBJECTSTORE_MODE"] = "push"
check("push mode is read from settings", objectstore.mode() == "push")

_c = S3()
put(_c)
check("push mode writes a new key", ops(_c) == ["HeadObject", "PutObject"], ops(_c))
check(
	"through the egress guard: the probe and the write both get passage",
	_c.passage == [True, True],
	_c.passage,
)
check("which ends with the call", not egress.in_passage())
check(
	"with If-None-Match so the store refuses a racing overwrite",
	_c.calls[-1][1].get("IfNoneMatch") == "*",
	_c.calls[-1][1],
)

_c = S3()
_c._make_api_call("GetObject", {"Bucket": "prod", "Key": "inherited.pdf"})
check("push-mode reads get passage, so inherited files resolve", _c.passage == [True], _c.passage)

_c = S3(existing={"taken.pdf"})
expect_raises("push mode refuses to overwrite a key", DevGuardBlocked, lambda: put(_c, "taken.pdf"))
check("the overwrite is never sent", ops(_c) == ["HeadObject"], ops(_c))

_c = S3(head_error="403")
put(_c)
check(
	"a write-only credential still writes, guarded by If-None-Match",
	ops(_c) == ["HeadObject", "PutObject"] and _c.calls[-1][1].get("IfNoneMatch") == "*",
	_c.calls,
)

_c = S3()
expect_raises(
	"an explicit If-Match overwrite is refused",
	DevGuardBlocked,
	lambda: _c._make_api_call("PutObject", {"Bucket": "prod", "Key": "k", "IfMatch": '"etag"'}),
)

_c = S3(existing={"big.bin"})
expect_raises(
	"a multipart upload onto an existing key fails before any part is sent",
	DevGuardBlocked,
	lambda: _c._make_api_call("CreateMultipartUpload", {"Bucket": "prod", "Key": "big.bin"}),
)

_c = S3()
_c._make_api_call("CompleteMultipartUpload", {"Bucket": "prod", "Key": "big.bin", "UploadId": "u"})
check("completing a multipart upload is conditional", _c.calls[-1][1].get("IfNoneMatch") == "*")

_c = S3()
_c._make_api_call("DeleteObject", {"Bucket": "prod", "Key": "a"})
check("push mode still drops deletes", ops(_c) == [], ops(_c))

expect_raises(
	"push mode still refuses tagging",
	DevGuardBlocked,
	lambda: S3()._make_api_call("PutObjectTagging", {"Bucket": "prod", "Key": "a", "Tagging": {}}),
)

# -- push mode against a store without conditional writes (Backblaze B2) --
#
# B2 answers any write carrying If-None-Match with 501 Not Implemented, so
# every push fails there. With conditional writes off, HeadObject is the only
# check left, and a write it cannot vouch for is refused rather than sent bare.

os.environ["FRAPPE_DEVGUARD_OBJECTSTORE_CONDITIONAL_WRITES"] = "false"

_c = S3()
put(_c)
check(
	"without conditional writes, a new key is written without If-None-Match",
	ops(_c) == ["HeadObject", "PutObject"] and "IfNoneMatch" not in _c.calls[-1][1],
	_c.calls,
)

_c = S3()
_c._make_api_call("CompleteMultipartUpload", {"Bucket": "prod", "Key": "big.bin", "UploadId": "u"})
check(
	"without conditional writes, completing a multipart upload is unconditional",
	"IfNoneMatch" not in _c.calls[-1][1],
	_c.calls,
)

_c = S3(existing={"taken.pdf"})
expect_raises(
	"without conditional writes, an existing key is still refused",
	DevGuardBlocked,
	lambda: put(_c, "taken.pdf"),
)
check("and the overwrite is never sent", ops(_c) == ["HeadObject"], ops(_c))

_c = S3(head_error="403")
expect_raises(
	"without conditional writes, a key HeadObject cannot check is refused",
	DevGuardBlocked,
	lambda: put(_c),
)
check("and the write is never sent", ops(_c) == ["HeadObject"], ops(_c))

os.environ.pop("FRAPPE_DEVGUARD_OBJECTSTORE_CONDITIONAL_WRITES")

# -- presigning --

check(
	"presigned reads are issued",
	_signers.generate_presigned_url(S3(), "get_object") == "https://signed/get_object",
)
expect_raises(
	"presigned writes are refused",
	DevGuardBlocked,
	lambda: _signers.generate_presigned_url(S3(), "put_object"),
)
expect_raises(
	"presigned POST is refused",
	DevGuardBlocked,
	lambda: _signers.generate_presigned_post(S3(), "prod", "k"),
)

# -- scope --

_c = S3(endpoint="http://127.0.0.1:9000")
_c._make_api_call("DeleteObject", {"Bucket": "local", "Key": "a"})
check("a loopback endpoint (local MinIO) is left alone", ops(_c) == ["DeleteObject"], ops(_c))

_c = S3(service="sqs")
_c._make_api_call("DeleteQueue", {"QueueUrl": "q"})
check("non-S3 clients are left alone", ops(_c) == ["DeleteQueue"], ops(_c))

os.environ["FRAPPE_DEVGUARD_OBJECTSTORE_MODE"] = "push"
_c = S3(service="ses")
_c._make_api_call("SendEmail", {"Destination": {}})
check("push mode opens no passage for other AWS services", _c.passage == [False], _c.passage)
os.environ.pop("FRAPPE_DEVGUARD_OBJECTSTORE_MODE")

os.environ["FRAPPE_DEVGUARD_DISABLE"] = "objectstore"
_c = S3()
_c._make_api_call("DeleteObject", {"Bucket": "prod", "Key": "a"})
check("FRAPPE_DEVGUARD_DISABLE=objectstore restores stock behaviour", ops(_c) == ["DeleteObject"])
os.environ["FRAPPE_DEVGUARD_OBJECTSTORE_MODE"] = "push"
_c = S3()
put(_c)
check(
	"and with the guard off, push mode opens no passage either: S3 is just another destination",
	_c.passage == [False],
	_c.passage,
)
os.environ.pop("FRAPPE_DEVGUARD_OBJECTSTORE_MODE")
os.environ.pop("FRAPPE_DEVGUARD_DISABLE")

os.environ["FRAPPE_DEVGUARD_OBJECTSTORE_MODE"] = "yolo"
check("an unknown mode falls back to local", objectstore.mode() == "local")
os.environ.pop("FRAPPE_DEVGUARD_OBJECTSTORE_MODE")

# -- cloud_storage's use_local, which only local mode forces --

_frappe_stub = types.ModuleType("frappe")
_frappe_stub.get_site_config = lambda: {"cloud_storage_settings": {"bucket": "prod"}}
objectstore._patch_get_site_config(_frappe_stub)
check(
	"local mode forces cloud_storage to local disk",
	_frappe_stub.get_site_config()["cloud_storage_settings"].get("use_local") is True,
)
os.environ["FRAPPE_DEVGUARD_OBJECTSTORE_MODE"] = "push"
check(
	"push mode leaves cloud_storage on the bucket",
	not _frappe_stub.get_site_config()["cloud_storage_settings"].get("use_local"),
)
os.environ.pop("FRAPPE_DEVGUARD_OBJECTSTORE_MODE")

# From Frappe 16 get_site_config lives in frappe.config, and frappe re-exports
# it. 17 (develop) makes the re-export lazy, through a module __getattr__, and
# frappe.init imports it from frappe.config at call time, past frappe's name.
# On 15, frappe.config is an unrelated package without it.


def frappe_and_config(*, lazy):
	config = types.ModuleType("frappe.config")
	config.get_site_config = lambda: {"cloud_storage_settings": {"bucket": "prod"}}
	frappe = types.ModuleType("frappe")
	if lazy:

		def __getattr__(name):
			if name != "get_site_config":
				raise AttributeError(name)
			sys.modules["frappe.config"] = config
			frappe.get_site_config = config.get_site_config
			return config.get_site_config

		frappe.__getattr__ = __getattr__
	else:
		sys.modules["frappe.config"] = config
		frappe.get_site_config = config.get_site_config
	return frappe, config


for _lazy, _label in ((False, "16's eager re-export"), (True, "17's lazy re-export")):
	_frappe, _config = frappe_and_config(lazy=_lazy)
	objectstore._patch_get_site_config(_frappe)
	check(
		f"{_label}: frappe.get_site_config forces local disk",
		_frappe.get_site_config()["cloud_storage_settings"].get("use_local") is True,
	)
	check(
		f"{_label}: so does frappe.config's" + (", which 17's frappe.init calls" if _lazy else ""),
		_config.get_site_config()["cloud_storage_settings"].get("use_local") is True,
	)
	del sys.modules["frappe.config"]

_unrelated = types.ModuleType("frappe.config")
sys.modules["frappe.config"] = _unrelated
_frappe_stub = types.ModuleType("frappe")
_frappe_stub.get_site_config = lambda: {"cloud_storage_settings": {"bucket": "prod"}}
objectstore._patch_get_site_config(_frappe_stub)
check("15's frappe.config package is left alone", not hasattr(_unrelated, "get_site_config"))
del sys.modules["frappe.config"]


del sys.modules["frappe"]

print()
if FAILURES:
	print(f"{len(FAILURES)} failure(s): {', '.join(FAILURES)}")
	sys.exit(1)
print("all devguard checks passed")
