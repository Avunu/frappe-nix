"""The two patches: Frappe's application loggers, and bench's own log.

Both run from post-import hooks, so neither Frappe nor bench is imported here.
Neither ever raises: if a target has moved, the process keeps its stock logging
— on stderr in production, since ``FRAPPE_STREAM_LOGGING`` is set — and says so
once. Unlike ``frappe_unixsock``, where a silent fallback means connecting to
somebody else's service, the worst case here is an unprefixed line.
"""

import inspect
import logging
import os
import sys

from ._format import JournaldFormatter, env_level


def warn(message):
	# Only ever reached with stderr on the journal (see install()), so the
	# prefix is read, not printed.
	sys.stderr.write(f"<4>frappe_journald {message}\n")


def patch_frappe_logger(module):
	"""Route ``frappe.utils.logger.get_logger`` to journald.

	Frappe builds every application logger (``frappe.logger("x")``) in
	``get_logger``, which ``frappe.logger`` imports inside its own body — so
	replacing the module attribute reaches every caller. The wrapper:

	* forces ``stream_only=True``. The default already is under
	  ``FRAPPE_STREAM_LOGGING``, but that is only a default, and a caller passing
	  ``stream_only=False`` would open ``../logs/<module>.log`` and
	  ``<site>/logs/<module>.log`` behind the journal's back;
	* swaps each handler's formatter for :class:`JournaldFormatter`. Frappe's own,
	  ``%(asctime)s %(levelname)s {module} %(message)s``, repeats what journald
	  records natively and carries no site.

	``FRAPPE_LOG_LEVEL`` replaces ``default_log_level`` rather than being applied
	per logger: ``get_logger`` reads it as ``frappe.log_level or
	default_log_level``, so an explicit ``set_log_level()`` — or the console's
	``frappe.log_level = 20`` while it stores history — still wins, exactly as it
	does over Frappe's own default. That default is ERROR outside the dev server,
	which silently drops every ``frappe.logger().warning()``.
	"""
	level = env_level()
	if level is not None and hasattr(module, "default_log_level"):
		module.default_log_level = level

	original = getattr(module, "get_logger", None)
	try:
		signature = inspect.signature(original) if original is not None else None
	except (TypeError, ValueError):
		signature = None
	if original is None or signature is None or "stream_only" not in signature.parameters:
		warn("frappe.utils.logger.get_logger has moved; Frappe's loggers keep their stock format")
		return

	def get_logger(*args, **kwargs):
		try:
			bound = signature.bind(*args, **kwargs)
		except TypeError:
			# Let Frappe raise its own error for a bad call.
			return original(*args, **kwargs)
		bound.arguments["stream_only"] = True
		logger = original(*bound.args, **bound.kwargs)

		# Frappe caches loggers, so on every call after the first this finds
		# the formatter already in place and does nothing.
		name = bound.arguments.get("module") or "frappe"
		for handler in logger.handlers:
			if isinstance(handler, logging.StreamHandler) and not isinstance(
				handler.formatter, JournaldFormatter
			):
				handler.setFormatter(JournaldFormatter(name))
		return logger

	get_logger.__wrapped__ = original  # ty: ignore[unresolved-attribute]
	get_logger.__doc__ = original.__doc__
	get_logger.__journald__ = "frappe.utils.logger.get_logger"  # ty: ignore[unresolved-attribute]
	module.get_logger = get_logger


def patch_bench_utils(module):
	"""Send bench's own log to stderr instead of ``logs/bench.log``.

	``bench.utils.setup_logging`` attaches a ``FileHandler("logs/bench.log")``
	whenever ``<bench_path>/logs`` exists, and a ``NullHandler`` otherwise. It
	also registers bench's ``LOG`` level and replaces ``Logger.log`` with a
	one-argument version that bench's own code calls everywhere — so it has to
	run, not be reimplemented.

	Pointing it at ``os.devnull`` takes the NullHandler branch (``/dev/null/logs``
	cannot exist) with everything else intact, and the NullHandler is then
	swapped for a stderr handler. Calling it normally and removing the
	FileHandler afterwards would not do: a FileHandler opens its file on
	construction, so ``bench.log`` would still be created on every invocation.

	``bench.cli`` binds ``setup_logging`` with a from-import, which runs after
	``bench.utils`` has finished importing and so after this hook — it binds the
	replacement.
	"""
	original = getattr(module, "setup_logging", None)
	if not callable(original):
		warn("bench.utils.setup_logging has moved; bench may still write logs/bench.log")
		return

	def setup_logging(bench_path="."):
		logger = original(bench_path=os.devnull)
		for handler in list(logger.handlers):
			if type(handler) is logging.NullHandler:
				logger.removeHandler(handler)
		# bench calls this more than once per process (cli.py, then
		# Bench.logging()); upstream stacks a handler each time, which with a
		# stream would print every line twice.
		if not any(isinstance(h.formatter, JournaldFormatter) for h in logger.handlers):
			handler = logging.StreamHandler()
			handler.setFormatter(JournaldFormatter("bench"))
			level = env_level()
			if level is not None:
				handler.setLevel(level)
			logger.addHandler(handler)
		return logger

	setup_logging.__wrapped__ = original  # ty: ignore[unresolved-attribute]
	setup_logging.__journald__ = "bench.utils.setup_logging"  # ty: ignore[unresolved-attribute]
	module.setup_logging = setup_logging
