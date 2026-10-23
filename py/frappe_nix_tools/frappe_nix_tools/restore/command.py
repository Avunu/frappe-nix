"""``bench restore``: restore the dev bench from a Frappe backup, an explicit file
or the newest one in the object store.

Imports no Frappe. Everything that needs Frappe is ``bench …`` run as a
subprocess, through the dev shell's ``bench`` wrapper, so this runs on
frappe-nix's tool interpreter rather than the bench's. Fetching is the
``frappe-nix-backup-fetch`` command (python-devops-nix's backup fetch layer),
which prints a JSON manifest.

Process semantics follow the bash it replaces: what bash ``exec``ed is
``os.execvp``ed; a failing step stops the restore with that step's exit status
(``set -e``); a long step that is interrupted takes the Ctrl-C with it and the
restore exits 130; the output of bench and of the fetch streams through.
"""

import hashlib
import json
import os
import secrets
import signal
import string
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn

from frappe_nix_tools.restore.args import USAGE, RestoreArgs, UsageError, parse_args
from frappe_nix_tools.restore.config import ConfigError, RestoreConfig, load

PREFIX = "bench restore"

#: The override for an unguarded restore, and the name it had before
#: (python-devops-nix docs/conventions.md, Tool-owned variables), read with a
#: warning through 1.x, until frappe-nix 2.0.0.
ALLOW_UNGUARDED = "FRAPPE_NIX_RESTORE_ALLOW_UNGUARDED"
ALLOW_UNGUARDED_LEGACY = "FRAPPE_RESTORE_ALLOW_UNGUARDED"

#: What neutralising switches off in the restored database. The scheduler's
#: own switch (System Settings, which `bench disable-scheduler` sets), and every
#: Email Account's sending and receiving: a dump of production carries live
#: SMTP and IMAP settings, and the queue it carries would otherwise go out
#: through them. devguard refuses those connections in this shell; this keeps
#: the restored database itself inert, below devguard, in case it is not.
MUTE_MAIL_SQL = "update `tabEmail Account` set enable_outgoing = 0, enable_incoming = 0"


class Abort(Exception):
	"""Stop with a message on stderr and this exit status."""

	def __init__(self, status: int, *lines: str) -> None:
		super().__init__(lines[0] if lines else "")
		self.status = status
		self.lines = lines


def say(*lines: str) -> None:
	for line in lines:
		print(line, flush=True)


def fail(*lines: str, status: int = 1) -> NoReturn:
	raise Abort(status, *lines)


def run(cmd: Sequence[str], *, env: Mapping[str, str] | None = None, capture: bool = False) -> str:
	"""Run CMD to completion; its failure is the restore's (set -e).

	stdout is captured when CAPTURE, and streams through otherwise; stderr
	always streams through. Ctrl-C reaches the child from the terminal along
	with this process: wait for it to finish with it, then exit 130.
	"""
	sys.stdout.flush()
	proc = subprocess.Popen(
		list(cmd),
		stdout=subprocess.PIPE if capture else None,
		env=dict(env) if env is not None else None,
		text=True,
	)
	try:
		out, _ = proc.communicate()
	except KeyboardInterrupt:
		# The terminal sent SIGINT to the whole foreground group, the child
		# included. If it came from elsewhere, pass it on.
		if proc.poll() is None:
			proc.send_signal(signal.SIGINT)
		proc.wait()
		raise Abort(130) from None
	if proc.returncode != 0:
		raise Abort(proc.returncode)
	return out or ""


def execvp(cmd: Sequence[str], env: Mapping[str, str] | None = None) -> NoReturn:
	sys.stdout.flush()
	sys.stderr.flush()
	if env is None:
		os.execvp(cmd[0], list(cmd))
	os.execvpe(cmd[0], list(cmd), dict(env))


def jq_alt(data: Mapping[str, Any], *keys: str) -> Any:
	"""jq's ``.a // .b // empty``: the first value that is neither null nor false."""
	for key in keys:
		value = data.get(key)
		if value is not None and value is not False:
			return value
	return None


def site_flag(site: str) -> list[str]:
	return ["--site", site] if site else []


def unguarded_allowed() -> bool:
	if os.environ.get(ALLOW_UNGUARDED):
		return os.environ[ALLOW_UNGUARDED] == "1"
	if os.environ.get(ALLOW_UNGUARDED_LEGACY):
		print(
			f"frappe-nix: {ALLOW_UNGUARDED_LEGACY} is deprecated; set {ALLOW_UNGUARDED} instead.",
			file=sys.stderr,
		)
		return os.environ[ALLOW_UNGUARDED_LEGACY] == "1"
	return False


def require_devguard(cfg: RestoreConfig) -> None:
	"""Refuse to put production's data into a bench with its guard rails off.

	Nix-time, not FRAPPE_DEVGUARD_ENABLED: `devguard.enable = false` is a
	persistent property of the bench, so every later `devenv up` is unguarded
	too. A per-command disable is scoped to that command and is nobody's
	problem.
	"""
	if cfg.devguard or not cfg.require_devguard or unguarded_allowed():
		return
	fail(
		f"{PREFIX}: refusing to restore into a bench with frappe-nix.devguard.enable = false.",
		"",
		"A restored database carries production's customers, its outgoing mail, its",
		"scheduled jobs and, with its site config, the key that decrypts every stored",
		"production credential. The guard rails are what keep a bench holding those",
		"from mailing customers or deleting objects out of the production bucket.",
		"They are a large reduction in blast radius, not an airgap, and turning them",
		"off makes this a real risk.",
		"",
		"  re-enable devguard, or",
		"  frappe-nix.restore.requireDevguard = false;   (a considered choice), or",
		f"  {ALLOW_UNGUARDED}=1 bench restore",
	)


def secrets_env(cfg: RestoreConfig) -> dict[str, str]:
	"""The environment the bench's secrets make, decrypted now and not before.

	Run here rather than at shell entry: agenix-shell re-runs rage on every
	source and shell entry runs on every direnv reload, so a passphrased key
	would prompt on every file save; and it exports the plaintext itself, which
	at shell entry would put credentials in the environment of every process in
	the session. What it adds reaches the fetch command and nothing else.
	"""
	env = dict(os.environ)
	if cfg.secrets_env:
		proc = subprocess.run([cfg.secrets_env], stdout=subprocess.PIPE, check=False)
		if proc.returncode != 0:
			fail(f"{PREFIX}: could not load this bench's secrets.", status=proc.returncode)
		for item in proc.stdout.split(b"\0"):
			name, eq, value = item.decode(errors="surrogateescape").partition("=")
			if eq and name:
				env[name] = value
	if cfg.prefix and not env.get("BACKUPS_PREFIX"):
		env["BACKUPS_PREFIX"] = cfg.prefix
	return env


def write_private(path: Path, data: Mapping[str, Any]) -> None:
	"""Replace PATH with DATA as JSON, readable by its owner only."""
	fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
	try:
		with os.fdopen(fd, "w") as f:
			json.dump(data, f, indent=2)
			f.write("\n")
		os.chmod(tmp, 0o600)
		os.replace(tmp, path)
	except BaseException:
		Path(tmp).unlink(missing_ok=True)
		raise


def seed_site(site: str) -> Path:
	"""sites/<site>/site_config.json, written if the site does not exist yet.

	`bench restore` cannot create a site: frappe.init raises IncorrectSitePath
	when site_config.json is absent, before it reads the dump. But that file is
	*all* it needs. restore_backup calls _new_site(force=True), which makes the
	site's directories and database itself, and make_site_config only writes when
	the file does not already exist, so a four-key seed both unblocks a fresh
	clone and survives untouched. A full `bench new-site` here would install
	every app into a database that is dropped seconds later.
	"""
	site_dir = Path("sites") / site
	site_cfg = site_dir / "site_config.json"
	# filelock() runs before _new_site and will not create its own parent
	# (frappe/utils/synchronization.py), so an older site directory that
	# predates locks/ would fail there rather than in the restore.
	(site_dir / "locks").mkdir(parents=True, exist_ok=True)
	if site_cfg.is_file():
		return site_cfg
	say(f"  creating {site} (it does not exist yet)")
	# frappe's own scheme when it picks a database name itself: "_" plus 16 hex,
	# which fits MariaDB's identifier limits and doubles as the database user.
	seeded: dict[str, str] = {
		"db_type": "mariadb",
		"db_name": "_" + hashlib.sha1(site.encode()).hexdigest()[:16],
		"db_password": "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16)),
	}
	if os.environ.get("FRAPPE_DB_SOCKET"):
		seeded["db_socket"] = os.environ["FRAPPE_DB_SOCKET"]
	write_private(site_cfg, seeded)
	return site_cfg


def carry_keys(site_cfg: Path, backup_cfg: Path, keys: Sequence[str]) -> None:
	"""Copy KEYS from production's site config into this site's.

	An allowlist, not a denylist: a denylist loses to the next app that invents
	`foo_api_secret`. Everything else production had (host_name, db_*, mail_*,
	cloud storage, maintenance_mode) stays behind.
	"""
	say(f"  carrying from the backup's site config: {' '.join(keys)}")
	ours = json.loads(site_cfg.read_text())
	theirs = json.loads(backup_cfg.read_text())
	ours.update({k: v for k, v in theirs.items() if k in keys})
	write_private(site_cfg, ours)


def resolve_encryption_key(site_cfg: Path, backup_cfg: Path | None) -> str:
	"""The key for an encrypted dump: this site's, else the backup's own.

	Always resolved here. Left to frappe, _restore falls back to
	get_or_generate_backup_encryption_key(), which *generates a fresh key and
	writes it into this site's config* before failing to decrypt, leaving the
	restore broken and the config wrong.
	"""
	keys = ("backup_encryption_key", "encryption_key")
	key = jq_alt(json.loads(site_cfg.read_text()), *keys)
	if not key and backup_cfg is not None:
		key = jq_alt(json.loads(backup_cfg.read_text()), *keys)
	if not key:
		fail(
			f"{PREFIX}: this backup is encrypted and no backup_encryption_key",
			"is available — not from the fetched site config, not from this site.",
			"Pass one with --encryption-key, or restore an unencrypted backup.",
		)
	return str(key)


def missing_apps(site: str) -> list[str]:
	"""Apps the restored database has installed and this bench does not.

	The dump carries production's Installed Applications. remove_missing_apps()
	only knows two legacy names, so an app production has and this bench does
	not survives into the restored database and takes `bench migrate` down with a
	bare ModuleNotFoundError.
	"""
	apps_txt = Path("sites/apps.txt")
	if not apps_txt.is_file():
		return []
	ours = set(apps_txt.read_text().split())
	proc = subprocess.run(
		["bench", *site_flag(site), "list-apps", "--format", "json"],
		stdout=subprocess.PIPE,
		stderr=subprocess.DEVNULL,
		text=True,
		check=False,
	)
	try:
		installed = json.loads(proc.stdout).get(site) or []
	except (ValueError, AttributeError):
		return []
	return [app for app in installed if app not in ours]


def neutralize(site: str) -> None:
	say("  neutralizing: scheduler paused, every Email Account's sending and receiving off")
	run(["bench", *site_flag(site), "disable-scheduler"])
	run(["bench", *site_flag(site), "execute", "frappe.db.sql", "--args", json.dumps([MUTE_MAIL_SQL])])


def restore_file(args: RestoreArgs, site: str) -> None:
	# No --db-socket: unlike `new-site`, `bench restore` has no such option, so
	# the socket arrives from the site's own site_config.json, or from
	# FRAPPE_DB_SOCKET through frappe/config.py. Both are set by the dev shell.
	assert args.sql_file is not None
	say(f"Restoring {site or 'all sites'} from {args.sql_file}…")
	cmd = [
		"bench",
		*site_flag(site),
		"restore",
		args.sql_file,
		"--db-root-username",
		"root",
		"--db-root-password",
		"",
		*args.passthru,
	]
	if args.encryption_key is not None:
		cmd += ["--encryption-key", args.encryption_key]
	if not args.neutralize:
		execvp(cmd)
	run(cmd)
	neutralize(site)


def restore_fetched(args: RestoreArgs, cfg: RestoreConfig, site: str) -> None:
	if not cfg.enable:
		fail(
			f"{PREFIX}: no backup source is configured for this bench.",
			"",
			"  Pass a file:   bench restore <dump.sql.gz>",
			"  Or enable fetching by declaring the backup-access secret:",
			'      frappe-nix.secrets.recipients.<you> = "ssh-ed25519 …";',
			"  then: edit-secret backup-access",
		)
	if not site:
		fail(
			f'{PREFIX}: this bench is multi-tenant (defaultSite = "") and',
			"FRAPPE_SITE is unset, so there is no site to restore into.",
			"Set FRAPPE_SITE in .env, or pass a file to restore explicitly.",
		)

	env = secrets_env(cfg)
	if args.list_only:
		execvp([cfg.fetch, "list"], env)

	# ── fetch ────────────────────────────────────────────────────────────
	want_public = args.files if args.files is not None else cfg.files == "all"
	want_private = args.private_files if args.private_files is not None else cfg.files in ("private", "all")
	fetch = [cfg.fetch, "fetch"]
	if args.at:
		fetch += ["--at", args.at]
	if want_public:
		fetch.append("--files")
	if want_private:
		fetch.append("--private-files")
	if args.no_cache:
		fetch.append("--no-cache")
	if not env.get("FRAPPE_NIX_BACKUP_CACHE") and not env.get("FRAPPE_BACKUP_CACHE"):
		# The legacy FRAPPE_BACKUP_CACHE is still honoured, by the fetch command
		# itself, through 1.x, until frappe-nix 2.0.0.
		env["FRAPPE_NIX_BACKUP_CACHE"] = os.path.join(
			os.environ.get("DEVENV_STATE", ".devenv/state"), "frappe-nix", "restore"
		)
	manifest = json.loads(run(fetch, env=env, capture=True))
	db_path = manifest["database"]
	conf_path = Path(manifest["site_config"]) if manifest.get("site_config") else None
	folder = manifest.get("folder") or manifest["name"]

	slug = manifest.get("slug", "")
	dev_slug = site.translate(str.maketrans(".:-", "___"))
	if slug != dev_slug:
		say(
			f"  note: the backup is of '{slug}', this bench is '{dev_slug}'.",
			"        Restoring across sites is fine — this site keeps its own",
			"        db_name and db_password, and file archives are extracted",
			"        with 'tar --strip 2', which discards the archive's own",
			"        site directory.",
		)

	site_cfg = seed_site(site)

	# ── the encryption keys ──────────────────────────────────────────────
	# Two different keys, and both matter: encryption_key decrypts every stored
	# Password and API-secret field in the dump (frappe/utils/password.py);
	# backup_encryption_key is the gpg passphrase for this dump and the next one
	# (frappe/utils/backups.py). Both live in the backup's own
	# site_config_backup.json, which is readable even for an "-enc" backup.
	if conf_path is not None and args.seed_config and cfg.carry_config_keys:
		carry_keys(site_cfg, conf_path, cfg.carry_config_keys)

	passthru = list(args.passthru)
	if args.encryption_key is not None:
		passthru += ["--encryption-key", args.encryption_key]
	if manifest.get("encrypted") and "--encryption-key" not in passthru:
		passthru += ["--encryption-key", resolve_encryption_key(site_cfg, conf_path)]

	# ── restore ──────────────────────────────────────────────────────────
	# --force by default: without it setup_database refuses with "Database …
	# already exists", so every restore after the first one fails. This is the
	# one destructive default here: it drops and recreates the site's database,
	# which is what "clone production" means. --no-force opts out.
	if args.force:
		passthru.append("--force")
	if manifest.get("files"):
		passthru += ["--with-public-files", manifest["files"]]
	if manifest.get("private_files"):
		passthru += ["--with-private-files", manifest["private_files"]]

	say(f"  restoring {site} from {folder}…")
	run(
		[
			"bench",
			"--site",
			site,
			"restore",
			db_path,
			"--db-root-username",
			"root",
			"--db-root-password",
			"",
			*passthru,
		]
	)

	# ── after ────────────────────────────────────────────────────────────
	if args.neutralize if args.neutralize is not None else cfg.neutralize:
		neutralize(site)

	do_migrate = args.migrate if args.migrate is not None else cfg.migrate
	missing = missing_apps(site)
	if missing:
		print(file=sys.stderr)
		print(
			f"  warning: the restored database has apps this bench does not: {' '.join(missing)}",
			file=sys.stderr,
		)
		print("           `bench migrate` will fail on them. Add each with:", file=sys.stderr)
		for app in missing:
			print(f"               bench get-app {app}", file=sys.stderr)
		do_migrate = False

	if do_migrate:
		say("  running bench migrate…")
		run(["bench-migrate"])

	say(f"✅ restored {site} from {folder}")


def restore(argv: list[str], cfg: RestoreConfig) -> int:
	args = parse_args(argv)
	if args.help:
		print(USAGE, end="")
		return 0

	root = os.environ.get("FRAPPE_BENCH_ROOT")
	if not root:
		fail(f"{PREFIX}: FRAPPE_BENCH_ROOT is not set; run this from the dev shell.")
	os.chdir(root)
	# Its own `bench …` calls re-enter the dev shell's wrapper, which hands them
	# to the real bench instead of back here.
	os.environ["_FRAPPE_BENCH_RAW"] = "1"
	site = os.environ.get("FRAPPE_SITE", "")

	if not args.list_only:
		require_devguard(cfg)
	if args.sql_file is not None:
		restore_file(args, site)
		return 0
	restore_fetched(args, cfg, site)
	return 0


def main(argv: list[str]) -> int:
	try:
		return restore(argv, load().restore)
	except UsageError as e:
		print(f"{PREFIX}: {e}", file=sys.stderr)
		return 1
	except ConfigError as e:
		print(f"{PREFIX}: {e}", file=sys.stderr)
		return 2
	except Abort as e:
		for line in e.lines:
			print(line, file=sys.stderr)
		return e.status
