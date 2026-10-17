"""Laying down template files without ever clobbering.

The template is rendered into a staging directory and only then installed, so
token substitution can never touch a file the bench already owns.
"""

import atexit
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any

from frappe_nix_tools.common.report import ConfigError as BlockError
from frappe_nix_tools.frappe_init import ui
from frappe_nix_tools.frappe_init.proc import git, probe_ok
from frappe_nix_tools.frappe_init.state import Run

# The managed .gitignore block is frappe-nix sync's implementation, so the two
# writers cannot drift.
from frappe_nix_tools.scaffold.blocks import GITIGNORE_BEGIN, GITIGNORE_END
from frappe_nix_tools.scaffold.blocks import gitignore as gitignore_with_block

__all__ = ["GITIGNORE_BEGIN", "GITIGNORE_END", "gitignore_with_block"]

TOKEN = re.compile(rb"@([A-Z_]+)@")


def tokens(st: Run) -> dict[str, str]:
	p = st.p
	return {
		"BENCH_NAME": st.name,
		"PROJECT_NAME": st.project_name,
		"SITE_NAME": st.site,
		"PYTHON": p.python,
		"NODEJS": p.node,
		"REQUIRES_PYTHON": p.requires_python,
		"PYTAG": p.pytag,
		"PYVER": p.pyver,
		"APP_NAME": st.app_name,
		"FRAPPE_VERSION": st.version,
		"FRAPPE_BRANCH": p.branch,
	}


def render_bytes(data: bytes, values: dict[str, str]) -> bytes:
	"""Every known @TOKEN@ in DATA replaced, in one pass: a value that itself
	holds a token is not expanded again, and an unknown token is left alone."""

	def sub(m: re.Match[bytes]) -> bytes:
		value = values.get(m.group(1).decode())
		return m.group(0) if value is None else value.encode()

	return TOKEN.sub(sub, data)


def render_overrides(data: bytes, overrides_json: str) -> bytes:
	"""The bench pyproject.toml's `"@OVERRIDES@"`, quotes and all, as the preset's
	array: the template quotes the token so the file is valid TOML at rest
	(lib/root-sync.nix reads it unrendered)."""
	return data.replace(b'"@OVERRIDES@"', overrides_json.encode(), 1)


def render_template(st: Run) -> Path:
	"""st.template rendered into a staging directory (once per run)."""
	if st.staging is not None:
		return st.staging
	staging = Path(tempfile.mkdtemp(prefix="frappe-init."))
	atexit.register(shutil.rmtree, staging, True)
	shutil.copytree(st.template, staging, dirs_exist_ok=True, symlinks=True)
	values = tokens(st)
	for root, _dirs, files in os.walk(staging):
		os.chmod(root, os.stat(root).st_mode | stat.S_IWUSR)
		for f in files:
			path = Path(root, f)
			path.chmod(path.stat().st_mode | stat.S_IWUSR)
			data = path.read_bytes()
			if TOKEN.search(data):
				path.write_bytes(render_bytes(data, values))
	pyproject = staging / "pyproject.toml"
	if pyproject.is_file():
		pyproject.write_bytes(render_overrides(pyproject.read_bytes(), st.p.overrides_json))
	st.staging = staging
	return staging


def staged_files(staging: Path) -> list[str]:
	return sorted(str(p.relative_to(staging)) for p in staging.rglob("*") if p.is_file())


def install_template(st: Run, *, keep: bool) -> None:
	"""Every staged file but .gitignore (install_gitignore_block splices that);
	with KEEP, a file that already exists is left as it is."""
	staging = render_template(st)
	for rel in staged_files(staging):
		if rel == ".gitignore":
			continue
		if keep and os.path.exists(rel):
			continue
		Path(rel).parent.mkdir(parents=True, exist_ok=True)
		shutil.copy(staging / rel, rel)
		ui.info(f"+ {rel}")


def install_gitignore_block(st: Run) -> None:
	staging = render_template(st)
	body = (staging / ".gitignore").read_text().rstrip("\n")
	path = Path(".gitignore")
	current = path.read_text() if path.is_file() else None
	try:
		new = gitignore_with_block(current, body)
	except BlockError as e:
		ui.die(str(e))
	if current == new:
		ui.info(". .gitignore already current")
	else:
		path.write_text(new)
		ui.info("+ .gitignore (frappe-nix managed block)")


def verify_not_ignored(st: Run) -> None:
	"""A flake's source tree is exactly its tracked files, so anything the build
	needs that .gitignore excludes is a silent, much later build failure."""
	bad = False
	paths = ["flake.nix", "pyproject.toml", ".envrc", "sites/apps.txt", "sites/apps.json"]
	paths += ["sites/common_site_config.json", "apps"]
	for app in st.vendored:
		paths += [f"apps/{app}/{app}/hooks.py", f"apps/{app}/pyproject.toml", f"apps/{app}/{app}/public"]
	for p in paths:
		if os.path.lexists(p) and probe_ok(git("check-ignore", "-q", "--", p)):
			ui.err(f"{p} is excluded by .gitignore")
			bad = True
	if bad:
		ui.die(
			"the bench .gitignore excludes files the Nix build needs (a flake's source tree is only its git-tracked files)"
		)


# ── sites/common_site_config.json ─────────────────────────────────────────


def frappe_web_port(name: str) -> int:
	"""The bench's web port, 8000..8899: byte for byte portOffsetFor in
	modules/devenv.nix (builtins.hashString "sha256" of the same bytes), from the
	bench's name rather than its path, because this file is committed."""
	return 8000 + int(hashlib.sha256(name.encode()).hexdigest()[:4], 16) % 900


#: Keys a dev bench must not carry. Production-only: host_name and http_port make
#: Frappe mint absolute URLs to the production host, and restart_* drive a
#: supervisor or systemd that is not there. Stale transport: db_host/db_port are
#: the unix socket in FRAPPE_DB_SOCKET now, the redis URLs FRAPPE_REDIS_*, and
#: redis_socketio and file_watcher_port are read by nothing.
DROPPED = (
	"host_name",
	"http_port",
	"frappe_user",
	"restart_supervisor_on_update",
	"restart_systemd_on_update",
	"db_host",
	"db_port",
	"redis_cache",
	"redis_queue",
	"redis_socketio",
	"file_watcher_port",
)


def deep_merge(a: Any, b: Any) -> Any:
	"""jq's `a * b`: objects merge key by key, recursively; anything else, b wins."""
	if isinstance(a, dict) and isinstance(b, dict):
		merged = dict(a)
		for k, v in b.items():
			merged[k] = deep_merge(merged[k], v) if k in merged else v
		return merged
	return b


def _jq_string(value: Any) -> str:
	"""`jq -r '.k // ""'` of one value."""
	if value is None or value is False:
		return ""
	if isinstance(value, str):
		return value
	return json.dumps(value)


def reconciled_site_config(
	cur: dict[str, Any], *, site: str, port: int, keep_root_pw: bool
) -> dict[str, Any]:
	"""Defaults, overridden by the bench's own file, overridden by the values the
	dev shell physically requires; then the keys it contradicts, removed."""
	defaults = {"default_site": site, "background_workers": 1, "gunicorn_workers": 1}
	forced: dict[str, Any] = {
		"use_redis_auth": False,
		"webserver_port": port,
		"socketio_port": port,
		"developer_mode": 1,
		"live_reload": True,
		"serve_default_site": True,
		"shallow_clone": True,
	}
	if not keep_root_pw:
		forced["mariadb_root_password"] = ""
	merged = deep_merge(deep_merge(defaults, cur), forced)
	return {k: v for k, v in merged.items() if k not in DROPPED}


def reconcile_common_site_config(st: Run) -> None:
	f = Path("sites/common_site_config.json")
	cur: dict[str, Any] = {}
	old = f.read_bytes() if f.is_file() else None
	if old is not None:
		try:
			parsed = json.loads(old)
		except ValueError:
			parsed = None
		if not isinstance(parsed, dict):
			ui.die(f"{f} is not a JSON object — fix or remove it and re-run")
		cur = parsed
		if _jq_string(cur.get("mariadb_root_password")) and not st.args.keep_db_root_password:
			st.warn(
				f"mariadb_root_password was set in {f} and has been blanked (this file is committed to git); --keep-db-root-password overrides"
			)
		db_host = _jq_string(cur.get("db_host"))
		if db_host not in ("", "localhost", "127.0.0.1"):
			st.warn(
				f"db_host was '{db_host}' and has been dropped; the devenv MariaDB is local and reached over the unix socket in FRAPPE_DB_SOCKET"
			)

	dropped = [k for k in DROPPED if k in cur]
	if dropped:
		ui.info(f"dropping superseded keys: {', '.join(dropped)}")

	new = reconciled_site_config(
		cur, site=st.site, port=frappe_web_port(st.name), keep_root_pw=st.args.keep_db_root_password
	)
	data = (json.dumps(new, indent=2, ensure_ascii=False) + "\n").encode()
	f.parent.mkdir(parents=True, exist_ok=True)
	if old == data:
		ui.info(f". {f} already current")
		return
	if old is not None:
		shutil.copyfile(f, ".frappe-nix-backup/common_site_config.json.orig")
	f.write_bytes(data)
	ui.info(f"+ {f}")
