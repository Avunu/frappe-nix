"""Self-contained tests for frappe_journald.

Runs without Frappe, without bench and without systemd. Stub modules stand in
for ``frappe.utils.logger`` and ``bench.utils``, written to disk so the
post-import hook sees a real import; ``JOURNAL_STREAM`` is pointed at this
process's own stderr, which is exactly the check systemd documents. The
assertions are about the bytes a line leaves with — its ``<N>`` prefix on every
line — and about which files are *not* created.

The runtime's copy of the formatter (``runtime/src/frappe_runtime/journald.py``)
is loaded by path and must render every record identically. Pass its path as the
first argument, or run from the repository and it is found relative to here.

Run directly (``python test_journald.py``) or via ``nix flake check``.
"""

import importlib
import importlib.util
import io
import logging
import os
import sys
import tempfile
import textwrap
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

FAILURES = []


def check(label, condition, detail=""):
	if condition:
		print(f"ok   {label}")
	else:
		print(f"FAIL {label} {detail}")
		FAILURES.append(label)


import frappe_journald  # noqa: E402
from frappe_journald import _format, _patches  # noqa: E402
from frappe_journald._format import JournaldFormatter, priority  # noqa: E402

SITE = "erp.example.com"


def clear_env():
	for name in ("JOURNAL_STREAM", "FRAPPE_SITE", "FRAPPE_LOG_LEVEL"):
		os.environ.pop(name, None)


def point_journal_at_stderr():
	st = os.fstat(2)
	os.environ["JOURNAL_STREAM"] = f"{st.st_dev}:{st.st_ino}"


def render(formatter, level, msg, name="frappe.test", exc=False):
	"""Format one record the way a handler would."""
	exc_info = None
	if exc:
		try:
			raise ValueError("boom")
		except ValueError:
			exc_info = sys.exc_info()
	record = logging.LogRecord(name, level, __file__, 1, msg, None, exc_info)
	return formatter.format(record)


# --------------------------------------------------------------------------
# priority mapping
# --------------------------------------------------------------------------
print("== priority ==")
for level, expected in (
	(logging.DEBUG, 7),
	(logging.INFO, 6),
	(logging.WARNING, 4),
	(logging.ERROR, 3),
	(logging.CRITICAL, 2),
	(15, 7),  # bench's LOG level rounds down to debug
	(25, 6),
	(logging.NOTSET, 7),
):
	check(f"level {level} -> <{expected}>", priority(level) == expected, priority(level))


# --------------------------------------------------------------------------
# the line format
# --------------------------------------------------------------------------
print()
print("== format ==")
clear_env()
fmt = JournaldFormatter("frappe.web")

line = render(fmt, logging.WARNING, "slow query")
check("no site known: no brackets", line == "<4>frappe.web slow query", repr(line))

os.environ["FRAPPE_SITE"] = SITE
line = render(fmt, logging.INFO, "hello")
check("site from FRAPPE_SITE", line == f"<6>frappe.web [{SITE}] hello", repr(line))
check("no asctime, no level name", "INFO" not in line and "20" not in line.split()[0], repr(line))

line = render(JournaldFormatter(), logging.ERROR, "x", name="frappe.runner")
check("module defaults to the logger name", line == f"<3>frappe.runner [{SITE}] x", repr(line))

# frappe.local.site outranks the environment: one process may serve many sites.
stub_frappe = types.ModuleType("frappe")
stub_frappe.local = types.SimpleNamespace(site="other.example.com")
sys.modules["frappe"] = stub_frappe
line = render(fmt, logging.INFO, "hello")
check("site from frappe.local", "[other.example.com]" in line, repr(line))
stub_frappe.local = types.SimpleNamespace()  # outside a request: no .site
line = render(fmt, logging.INFO, "hello")
check("frappe.local without a site falls back to FRAPPE_SITE", f"[{SITE}]" in line, repr(line))
del sys.modules["frappe"]

text = render(fmt, logging.ERROR, "failed\nsecond line", exc=True)
lines = text.split("\n")
check("multi-line record spans several lines", len(lines) > 4, len(lines))
check("every line carries the record's priority", all(l.startswith("<3>") for l in lines), text)
check("only the first line carries the head", lines[0] == f"<3>frappe.web [{SITE}] failed", lines[0])
check("the traceback is kept", any("ValueError: boom" in l for l in lines), text)
check("no trailing empty entry", not text.endswith("\n") and lines[-1] != "<3>", repr(lines[-1]))

line = render(fmt, logging.WARNING, "trailing\n")
check("a trailing newline is not a second entry", line == f"<4>frappe.web [{SITE}] trailing", repr(line))
clear_env()


# --------------------------------------------------------------------------
# JOURNAL_STREAM detection
# --------------------------------------------------------------------------
print()
print("== journal_stream ==")
clear_env()
check("unset: not the journal", not _format.journal_stream())
os.environ["JOURNAL_STREAM"] = "garbage"
check("malformed: not the journal", not _format.journal_stream())
os.environ["JOURNAL_STREAM"] = "1:1"
check("inherited, stderr elsewhere: not the journal", not _format.journal_stream())
point_journal_at_stderr()
check("dev:inode of fd 2: the journal", _format.journal_stream())
clear_env()

frappe_journald._INSTALLED = False
before = list(sys.meta_path)
frappe_journald.install()
check("install() off the journal hooks nothing", sys.meta_path == before)


# --------------------------------------------------------------------------
# frappe.utils.logger
# --------------------------------------------------------------------------
print()
print("== frappe.utils.logger ==")


def make_frappe_logger():
	"""A stand-in for frappe/utils/logger.py with v16's get_logger signature."""
	stub = types.ModuleType("frappe.utils.logger")
	stub.default_log_level = logging.ERROR
	stub.loggers = {}
	stub.calls = []

	def get_logger(
		module=None,
		with_more_info=False,
		allow_site=True,
		filter=None,
		max_size=100_000,
		file_count=20,
		stream_only=False,
	):
		stub.calls.append(stream_only)
		key = f"{module}-all"
		if key in stub.loggers:
			return stub.loggers[key]
		logger = logging.getLogger(f"journald-test-{key}-{len(stub.calls)}")
		logger.propagate = False
		logger.setLevel(stub.default_log_level)
		handler = logging.StreamHandler(io.StringIO())
		handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s x %(message)s"))
		logger.addHandler(handler)
		stub.loggers[key] = logger
		return logger

	stub.get_logger = get_logger
	return stub


clear_env()
os.environ["FRAPPE_LOG_LEVEL"] = "warning"
os.environ["FRAPPE_SITE"] = SITE
mod = make_frappe_logger()
_patches.patch_frappe_logger(mod)
check("FRAPPE_LOG_LEVEL replaces default_log_level", mod.default_log_level == logging.WARNING)

logger = mod.get_logger("payments")
check("stream_only is forced on", mod.calls[-1] is True, mod.calls)
mod.get_logger("other", False, True, None, 1, 1, False)
check("...even when passed positionally as False", mod.calls[-1] is True, mod.calls)

handler = logger.handlers[0]
check("the handler gets the journald formatter", isinstance(handler.formatter, JournaldFormatter))
logger.warning("card declined")
logger.info("below the level")
out = handler.stream.getvalue()
check("a line is written with its priority", out == f"<4>payments [{SITE}] card declined\n", repr(out))

same = mod.get_logger("payments")
check("cached loggers pass through untouched", same is logger and len(logger.handlers) == 1)
check("the wrapper still identifies itself", getattr(mod.get_logger, "__journald__", None))

os.environ["FRAPPE_LOG_LEVEL"] = "nonsense"
mod = make_frappe_logger()
_patches.patch_frappe_logger(mod)
check("an unknown level leaves Frappe's default", mod.default_log_level == logging.ERROR)

moved = types.ModuleType("frappe.utils.logger")
moved.get_logger = lambda module=None: None  # no stream_only parameter any more
original = moved.get_logger
_patches.patch_frappe_logger(moved)
check("a moved target is left alone, not broken", moved.get_logger is original)
clear_env()


# --------------------------------------------------------------------------
# bench.utils
# --------------------------------------------------------------------------
print()
print("== bench.utils ==")

BENCH_LOG_LEVEL = 15


def make_bench_utils():
	"""bench.utils.setup_logging as bench 5.x ships it."""
	module = types.ModuleType("bench.utils")

	def setup_logging(bench_path="."):
		logging.addLevelName(BENCH_LOG_LEVEL, "LOG")
		module.log_override_installed = True
		if os.path.exists(os.path.join(bench_path, "logs")):
			hdlr = logging.FileHandler(os.path.join(bench_path, "logs", "bench.log"))
		else:
			hdlr = logging.NullHandler()
		logger = logging.getLogger("journald-test-bench")
		hdlr.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
		logger.addHandler(hdlr)
		logger.setLevel(logging.DEBUG)
		return logger

	module.setup_logging = setup_logging
	return module


clear_env()
bench_dir = tempfile.mkdtemp()
os.makedirs(os.path.join(bench_dir, "logs"))
cwd = os.getcwd()
os.chdir(bench_dir)
try:
	utils = make_bench_utils()
	_patches.patch_bench_utils(utils)
	logger = utils.setup_logging()
	logger = utils.setup_logging(bench_path=bench_dir)
finally:
	os.chdir(cwd)

check("bench.log is never created", not os.path.exists(os.path.join(bench_dir, "logs", "bench.log")))
check("upstream's own setup still ran", getattr(utils, "log_override_installed", False))
check("no NullHandler left behind", not any(type(h) is logging.NullHandler for h in logger.handlers))
journald_handlers = [h for h in logger.handlers if isinstance(h.formatter, JournaldFormatter)]
check("exactly one stderr handler, however often it is called", len(journald_handlers) == 1, logger.handlers)
check("...and it is a stream", type(journald_handlers[0]) is logging.StreamHandler)

stream = io.StringIO()
journald_handlers[0].setStream(stream)
logger.warning("bench update failed")
check(
	"bench lines carry a priority",
	stream.getvalue() == "<4>bench bench update failed\n",
	repr(stream.getvalue()),
)
logger.handlers.clear()

moved = types.ModuleType("bench.utils")
_patches.patch_bench_utils(moved)
check("a missing setup_logging is left alone", not hasattr(moved, "setup_logging"))


# --------------------------------------------------------------------------
# the hook, end to end
# --------------------------------------------------------------------------
print()
print("== install() on the journal ==")
stubs = tempfile.mkdtemp()
for path, body in {
	"frappe/__init__.py": "",
	"frappe/utils/__init__.py": "",
	"frappe/utils/logger.py": """
        import logging
        default_log_level = logging.ERROR
        def get_logger(module=None, with_more_info=False, allow_site=True, filter=None,
                       max_size=100_000, file_count=20, stream_only=False):
            return logging.getLogger("journald-e2e")
    """,
	"bench/__init__.py": "",
	"bench/utils/__init__.py": """
        import logging
        def setup_logging(bench_path="."):
            return logging.getLogger("journald-e2e-bench")
    """,
	# What bench.cli does: a from-import, bound after bench.utils has loaded.
	"bench/cli.py": "from bench.utils import setup_logging\n",
}.items():
	full = os.path.join(stubs, path)
	os.makedirs(os.path.dirname(full), exist_ok=True)
	with open(full, "w") as f:
		f.write(textwrap.dedent(body))
sys.path.insert(0, stubs)

clear_env()
point_journal_at_stderr()
os.environ["FRAPPE_LOG_LEVEL"] = "info"
frappe_journald._INSTALLED = False
frappe_journald.install()
logger_mod = importlib.import_module("frappe.utils.logger")
cli = importlib.import_module("bench.cli")
check("frappe.utils.logger is patched on import", getattr(logger_mod.get_logger, "__journald__", None))
check("FRAPPE_LOG_LEVEL applied on import", logger_mod.default_log_level == logging.INFO)
check("bench.cli binds the patched setup_logging", getattr(cli.setup_logging, "__journald__", None))
clear_env()
for name in [m for m in sys.modules if m == "frappe" or m.startswith(("frappe.", "bench"))]:
	del sys.modules[name]


# --------------------------------------------------------------------------
# the runtime's copy
# --------------------------------------------------------------------------
print()
print("== runtime copy ==")
runtime_path = (
	sys.argv[1]
	if len(sys.argv) > 1
	else os.path.join(HERE, "..", "..", "..", "runtime", "src", "frappe_runtime", "journald.py")
)
spec = importlib.util.spec_from_file_location("runtime_journald", runtime_path)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)

check(
	"priority mapping agrees at every level",
	all(runtime.priority(n) == priority(n) for n in range(0, 61)),
)
for site in (None, SITE):
	clear_env()
	if site:
		os.environ["FRAPPE_SITE"] = site
	for module in (None, "frappe.web"):
		for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL):
			for exc in (False, True):
				ours = render(JournaldFormatter(module), level, "a\nb", exc=exc)
				theirs = render(runtime.JournaldFormatter(module), level, "a\nb", exc=exc)
				if ours != theirs:
					check(
						f"render agrees ({site}, {module}, {level}, {exc})", False, f"{ours!r} != {theirs!r}"
					)
check(
	"render agrees for every level, site, module and traceback",
	not any("render agrees (" in f for f in FAILURES),
)

point_journal_at_stderr()
check("journal detection agrees", runtime.journal_stream() is _format.journal_stream() is True)
clear_env()
check("...off the journal too", runtime.journal_stream() is _format.journal_stream() is False)

print()
if FAILURES:
	print(f"{len(FAILURES)} failure(s): {', '.join(FAILURES)}")
	sys.exit(1)
print("all journald checks passed")
