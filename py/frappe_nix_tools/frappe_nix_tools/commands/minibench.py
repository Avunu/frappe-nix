"""``frappe-nix minibench``: a sparse, fake bench built from the app's ``flake.lock`` (spec §5.9, S19).

``frappe-nix minibench --mode customizations|doctypes --dest D``. For frappe and each
sibling, a blob-less, sparse clone of the locked repository (``github``: ``https://<host>/
<owner>/<repo>``, ``gitlab``: ``https://<host>/<owner>/<repo>``, ``git``: its ``url``;
authenticated with ``FRAPPE_NIX_FETCH_TOKEN`` when set) is checked out at the locked rev
into ``D/apps/<name>`` with the non-cone patterns ``/*/modules.txt``, ``/*/hooks.py``,
``/pyproject.toml``, ``/*/*/doctype/**/*.json``, ``/*/*/custom/*.json`` and
``/*/fixtures/*.json``. Then ``D/sites/apps.txt`` and ``D/sites/apps.json`` list frappe, the
siblings and the app, and ``D/env`` is created.

- ``customizations`` (the ``test-utils`` hooks ``validate_customizations`` and
  ``clean_customized_doctypes``, run in ``lint``): ``git worktree add --detach D/apps/<app> HEAD``;
  the caller then runs the manual-stage hooks from there.
- ``doctypes`` (``frappe-types gen-doctypes``, in ``typecheck``): a copy of the app's
  ``/<app>/*/doctype/**/*.json``, ``/<app>/*/custom/*.json`` and ``/<app>/fixtures/*.json``.

The guard: exit 1 unless ``D/sites``, ``D/env`` and ``D/apps/frappe/frappe/modules.txt``
exist (``--check-only`` runs the guard alone). Without the layout, the two test_utils
hooks are silent no-ops, so a missing bench must fail rather than pass.
"""

import argparse
import dataclasses
import json
import shutil
import sys
from pathlib import Path

from frappe_nix_tools.common import config, flakelock, repo
from frappe_nix_tools.common.pins import _git
from frappe_nix_tools.common.report import CLEAN, DRIFT, ConfigError
from frappe_nix_tools.scaffold import context, globs

SPARSE = (
	"/*/modules.txt",
	"/*/hooks.py",
	"/pyproject.toml",
	"/*/*/doctype/**/*.json",
	"/*/*/custom/*.json",
	"/*/fixtures/*.json",
)


def clone_url(pin: flakelock.Pin) -> str:
	if pin.type == "git":
		return pin.url
	host = pin.host or ("gitlab.com" if pin.type == "gitlab" else "github.com")
	return f"https://{host}/{pin.owner.replace('%2F', '/')}/{pin.repo}"


def sparse_clone(pin: flakelock.Pin, dest: Path) -> None:
	"""A blob-less, non-cone sparse checkout of ``pin`` at its locked rev into ``dest``."""
	url = clone_url(pin)
	pin = dataclasses.replace(pin, url=url)
	if dest.exists():
		shutil.rmtree(dest)
	dest.parent.mkdir(parents=True, exist_ok=True)
	_git(pin, "clone", "--quiet", "--filter=blob:none", "--no-checkout", "--sparse", "--", url, str(dest))
	_git(pin, "sparse-checkout", "set", "--no-cone", *SPARSE, cwd=dest)
	_git(pin, "-c", "advice.detachedHead=false", "checkout", "--quiet", pin.rev, cwd=dest)


def guard(dest: Path) -> list[str]:
	"""What the layout lacks; empty when the bench is usable."""
	need = (dest / "sites", dest / "env", dest / "apps" / "frappe" / "frappe" / "modules.txt")
	return [str(p) for p in need if not p.exists()]


def copy_app_doctypes(root: Path, app: str, target: Path) -> None:
	patterns = [f"{app}/*/doctype/**/*.json", f"{app}/*/custom/*.json", f"{app}/fixtures/*.json"]
	for path in globs.select(patterns, repo.ls_files(root)):
		out = target / path
		out.parent.mkdir(parents=True, exist_ok=True)
		shutil.copy2(root / path, out)
	# The app is an app: a hooks.py and modules.txt make frappe-types see it.
	for name in ("hooks.py", "modules.txt"):
		if (root / app / name).is_file():
			(target / app).mkdir(parents=True, exist_ok=True)
			shutil.copy2(root / app / name, target / app / name)


def build(root: Path, mode: str, dest: Path, lock_path: Path | None = None) -> list[str]:
	"""Build the bench; returns the guard's findings."""
	resolved = config.resolve(root / "pyproject.toml")
	cfg = resolved.cfg
	app = repo.app_name(root)
	lock = flakelock.load(lock_path or root / "flake.lock")
	major = int(cfg["frappe-major"])
	sibs = context.siblings(cfg, major)
	dest = dest.resolve()
	(dest / "sites").mkdir(parents=True, exist_ok=True)
	(dest / "env").mkdir(parents=True, exist_ok=True)
	names = ["frappe"]
	sparse_clone(flakelock.locked_pin(lock, "frappe"), dest / "apps" / "frappe")
	for s in sibs:
		sparse_clone(flakelock.locked_pin(lock, s["input"]), dest / "apps" / s["name"])
		names.append(s["name"])
	target = dest / "apps" / app
	if target.exists():
		if mode == "customizations":
			repo.git(root, "worktree", "remove", "--force", str(target))
		if target.exists():
			shutil.rmtree(target)
	if mode == "customizations":
		repo.git(root, "worktree", "prune")
		repo.git(root, "worktree", "add", "--detach", str(target), "HEAD")
	else:
		copy_app_doctypes(root, app, target)
	names.append(app)
	(dest / "sites" / "apps.txt").write_text("\n".join(names) + "\n")
	(dest / "sites" / "apps.json").write_text(
		json.dumps({n: {"is_repo": True} for n in names}, indent=1) + "\n"
	)
	return guard(dest)


def run(args: argparse.Namespace) -> int:
	dest = Path(args.dest)
	if args.check_only:
		missing = guard(dest)
	else:
		root = repo.find_up("pyproject.toml").parent
		if args.mode not in ("customizations", "doctypes"):
			raise ConfigError(f"--mode is customizations or doctypes, not {args.mode!r}")
		missing = build(root, args.mode, dest)
	for m in missing:
		print(f"frappe-nix minibench: the bench layout is missing {m}", file=sys.stderr)
	if missing:
		return DRIFT
	print(dest.resolve())
	return CLEAN


def register(subparsers: argparse._SubParsersAction) -> None:
	p = subparsers.add_parser(
		"minibench",
		help="build a sparse fake bench from flake.lock (test-utils hooks, gen-doctypes)",
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	p.add_argument("--mode", default="customizations", help="customizations or doctypes")
	p.add_argument("--dest", required=True, help="the bench directory to create")
	p.add_argument("--check-only", action="store_true", help="run only the layout guard on --dest")
	p.set_defaults(func=run)
