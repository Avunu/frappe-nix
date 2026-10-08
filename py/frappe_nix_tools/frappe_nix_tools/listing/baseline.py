"""The registry semgrep baseline, a multiset (S21, §2.21), and rule L7 (§5.2).

``marketplace/semgrep-baseline.json``::

    {
        "schema": 1,
        "marketplace_rev": "<sha>",
        "findings": [{"rule": "…", "path": "…", "line_sha1": "<sha1>", "count": 12, "reason": "…"}],
    }

A finding is keyed ``(rule, path, sha1(stripped first matched line))``: line numbers move,
and OSS semgrep returns ``"requires login"`` as its own fingerprint (and as the matched
text), so the line is read from the scanned file. Identical lines share a key and are
counted: twelve identical ``frappe.db.commit()`` lines are one entry with ``count = 12``,
and a thirteenth is a new finding. For every key, the count found must equal the
baseline's (absent = 0). More is a new finding; fewer means the entry is stale and its
count must come down. So the baseline only shrinks, and ``--release`` wants it empty.

The scan is the registry's own: ``validation/semgrep_check.py`` from the pinned
``frappe/marketplace`` tree, ``SemgrepValidator(<checkout>, <app>)``, run over a copy of
the tracked files (what the registry clones), with ``EIO_BACKEND=posix``.
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shlex
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from frappe_nix_tools.common import repo
from frappe_nix_tools.common.report import ConfigError, EnvError
from frappe_nix_tools.scaffold import jsonfmt

PATH = "marketplace/semgrep-baseline.json"
SEED: dict[str, Any] = {"schema": 1, "findings": []}
ENTRY_KEYS = {"rule", "path", "line_sha1", "count", "reason"}


def line_sha1(text: str) -> str:
	"""The sha1 of a matched first line, stripped of surrounding whitespace."""
	return hashlib.sha1(text.strip().encode()).hexdigest()


@dataclass(frozen=True)
class Hit:
	"""One semgrep finding, as L7 keys it."""

	rule: str
	path: str
	line: int
	text: str
	message: str = ""

	@property
	def key(self) -> tuple[str, str, str]:
		return (self.rule, self.path, line_sha1(self.text))


def problems(text: str) -> list[str]:
	"""Every way ``text`` breaks the baseline's shape; empty when it holds."""
	try:
		doc = json.loads(text)
	except json.JSONDecodeError as e:
		return [f"not JSON: {e}"]
	if not isinstance(doc, dict):
		return ['must be an object {"schema": 1, "findings": [...]}']
	out = []
	unknown = set(doc) - {"schema", "marketplace_rev", "findings"}
	if unknown:
		out.append(f"unknown key(s) {sorted(unknown)}")
	if doc.get("schema") != 1:
		out.append("schema must be 1")
	if "marketplace_rev" in doc and not isinstance(doc["marketplace_rev"], str):
		out.append("marketplace_rev must be a string")
	findings = doc.get("findings")
	if not isinstance(findings, list):
		return [*out, "findings must be a list"]
	seen: set[tuple] = set()
	for i, entry in enumerate(findings):
		where = f"findings[{i}]"
		if not isinstance(entry, dict):
			out.append(f"{where} is not an object")
			continue
		if set(entry) - ENTRY_KEYS:
			out.append(f"{where}: unknown key(s) {sorted(set(entry) - ENTRY_KEYS)}")
		for key in ("rule", "path", "line_sha1"):
			if not isinstance(entry.get(key), str) or not entry.get(key):
				out.append(f"{where}: {key} must be a non-empty string")
		count = entry.get("count")
		if not isinstance(count, int) or isinstance(count, bool) or count < 1:
			out.append(f"{where}: count must be an integer of at least 1")
		if "reason" in entry and not isinstance(entry["reason"], str):
			out.append(f"{where}: reason must be a string")
		key = (entry.get("rule"), entry.get("path"), entry.get("line_sha1"))
		if key in seen:
			out.append(
				f"{where}: ({key[0]}, {key[1]}, {key[2]}) is listed twice; one entry carries the count"
			)
		seen.add(key)
	return out


def load(root: Path) -> dict[tuple[str, str, str], dict]:
	"""The baseline's entries by key; a missing file is an empty baseline, a malformed one exit 2."""
	path = root / PATH
	if not path.is_file():
		return {}
	text = path.read_text()
	bad = problems(text)
	if bad:
		raise ConfigError(f"{PATH}: {bad[0]}")
	return {(e["rule"], e["path"], e["line_sha1"]): e for e in json.loads(text)["findings"]}


def compare(hits: list[Hit], entries: dict[tuple[str, str, str], dict]) -> list[tuple[str, str]]:
	"""L7, as ``(path, problem)``: for every key, the number found must equal the baseline's count
	(absent = 0). A new finding is reported on its file, a stale entry on the baseline."""
	found = Counter(h.key for h in hits)
	where: dict[tuple[str, str, str], list[Hit]] = {}
	for h in hits:
		where.setdefault(h.key, []).append(h)
	out = []
	for key in sorted(set(found) | set(entries)):
		have, allowed = found.get(key, 0), int(entries.get(key, {}).get("count", 0))
		rule, path, _ = key
		if have > allowed:
			lines = ", ".join(f"{h.path}:{h.line}" for h in where[key])
			sample = where[key][0]
			out.append(
				(
					path,
					f"new {rule} finding(s): {have} found, the baseline allows {allowed} ({lines}):"
					f" {sample.message or sample.text.strip()}",
				)
			)
		elif have < allowed:
			fix = "remove the entry" if have == 0 else f"lower its count to {have}"
			out.append(
				(
					PATH,
					f"stale entry for {rule} in {path} (line_sha1 {key[2][:12]}): count {allowed}, found {have}:"
					f" {fix} (the baseline only shrinks)",
				)
			)
	return out


def render(hits: list[Hit], marketplace_rev: str, previous: dict[tuple[str, str, str], dict]) -> str:
	"""The baseline that accepts exactly ``hits``, keeping each kept entry's reason."""
	counts = Counter(h.key for h in hits)
	findings = []
	for key in sorted(counts):
		entry = {"rule": key[0], "path": key[1], "line_sha1": key[2], "count": counts[key]}
		reason = previous.get(key, {}).get("reason")
		if reason:
			entry["reason"] = reason
		findings.append(entry)
	doc: dict[str, Any] = {"schema": 1}
	if marketplace_rev:
		doc["marketplace_rev"] = marketplace_rev
	doc["findings"] = findings
	return jsonfmt.dumps(doc)


# --- the scan ----------------------------------------------------------------------------


def export(root: Path, dest: Path) -> None:
	"""The tracked files under ``root``, as they are in the work tree, copied to ``dest``: what
	the registry clones (no ``node_modules``, no bench, no pins). Links are copied as links."""
	for rel in repo.ls_files(root):
		src = root / rel
		target = dest / rel
		target.parent.mkdir(parents=True, exist_ok=True)
		if src.is_symlink():
			target.symlink_to(os.readlink(src))
		elif src.is_file():
			shutil.copy2(src, target)


def _semgrep_on_path(root: Path, shim: Path, floor: str) -> dict[str, str]:
	"""The environment the scan runs in: ``semgrep`` on PATH, from PATH itself, else the app's
	``tools/`` project when it pins semgrep, else ``uvx`` at the floor version."""
	env = dict(os.environ, EIO_BACKEND="posix")
	if shutil.which("semgrep"):
		return env
	tools = root / "tools" / "pyproject.toml"
	if tools.is_file() and "semgrep" in tools.read_text() and shutil.which("uv"):
		command = f'exec uv run --frozen --project {shlex.quote(str(root / "tools"))} semgrep "$@"'
	elif shutil.which("uvx"):
		command = f'exec uvx --from {shlex.quote(f"semgrep=={floor}")} semgrep "$@"'
	else:
		raise EnvError(
			"L7 needs semgrep: it is not on PATH, and neither the app's tools/ project nor uvx can run it"
		)
	shim.mkdir(parents=True, exist_ok=True)
	script = shim / "semgrep"
	script.write_text(f"#!/bin/sh\n{command}\n")
	script.chmod(0o755)
	env["PATH"] = f"{shim}{os.pathsep}{env.get('PATH', '')}"
	return env


def _validator_class(marketplace: Path) -> Any:
	"""``SemgrepValidator`` from the pinned tree's ``validation/semgrep_check.py``."""
	source = marketplace / "validation" / "semgrep_check.py"
	if not source.is_file():
		raise EnvError(f"{source} does not exist: the marketplace pin has no registry semgrep check")
	validation = str(source.parent)
	saved_path, saved_utils = (
		list(sys.path),
		{k: v for k, v in sys.modules.items() if k == "utils" or k.startswith("utils.")},
	)
	for name in saved_utils:
		del sys.modules[name]
	try:
		spec = importlib.util.spec_from_file_location("frappe_nix_marketplace_semgrep_check", source)
		if spec is None or spec.loader is None:
			raise EnvError(f"{source} cannot be loaded")
		module = importlib.util.module_from_spec(spec)
		sys.path.insert(0, validation)
		spec.loader.exec_module(module)
	finally:
		sys.path[:] = saved_path
		for name in [k for k in sys.modules if k == "utils" or k.startswith("utils.")]:
			del sys.modules[name]
		sys.modules.update(saved_utils)
	return module.SemgrepValidator


def scan(root: Path, app: str, marketplace: Path, floor: str = "1.179.0") -> tuple[list[Hit], list[Hit], str]:
	"""``(blocking, advisory, log)``: the registry's semgrep check over the tracked tree."""
	validator_class = _validator_class(marketplace)
	with tempfile.TemporaryDirectory(prefix="frappe-listing-") as tmp:
		work = Path(tmp) / app
		export(root, work)
		env = _semgrep_on_path(root, Path(tmp) / "bin", floor)
		log = io.StringIO()
		saved = dict(os.environ)
		os.environ.clear()
		os.environ.update(env)
		try:
			validator = validator_class(work, app)
			with contextlib.redirect_stdout(log):
				validator.validate()
		except RuntimeError as e:
			raise EnvError(f"L7: the registry semgrep scan failed: {e}") from e
		finally:
			os.environ.clear()
			os.environ.update(saved)

		def hit(finding: Any) -> Hit:
			path = str(finding.path)
			line = int(finding.line or 0)
			try:
				text = (work / path).read_text(errors="replace").split("\n")[line - 1] if line else ""
			except (OSError, IndexError):
				text = ""
			return Hit(str(finding.rule), path, line, text, str(finding.message))

		blocking = [hit(f) for f in validator.findings]
		advisory = [hit(f) for f in validator.notes]
	return blocking, advisory, log.getvalue()
