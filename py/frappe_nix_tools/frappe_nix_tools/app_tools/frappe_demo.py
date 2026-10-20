"""frappe-demo: a repeatable demo site for the app (docs/app-standards/spec.md §5.4).

  frappe-demo [--site S] [--fresh] [--recreate-site] [--erpnext-demo | --no-erpnext-demo]
              [--date YYYY-MM-DD] [--seed N] [--no-up]

Run by lib/standards/tools/frappe-demo.nix inside an opted-in app's dev shell,
which supplies devenv, process-compose, provision-site, bench and mariadb-admin.
The defaults are the `demo` block of marketplace/screenshots.ts when the app has
one, else the demo module's parameters.

  1. bring the bench up as frappe-test's stage 1 does (unless --no-up), and with
     --fresh, or when the site is missing, provision it. --fresh on a site that
     exists drops its database: outside CI (CI=true) that needs --recreate-site or
     a yes at the prompt, as frappe-test's does;
  2-5. lib/demo/frappe_demo.py with the bench's interpreter: the setup wizard with
     fixed arguments, erpnext's demo data (when asked for and installed), the
     app's <app>.demo.setup(ctx), one commit, sites/<site>/demo.json.

The bench is left up (frappe-test --down, or process-compose down, stops it).
Under frappe-shots (§5.5), FRAPPE_NIX_SHOTS_PRELOAD and FRAPPE_NIX_SHOTS_FAKETIME
(a UTC time; the script runs with TZ=UTC) put the demo script on libfaketime's
clock, so what it makes is dated on the demo day.

Exit codes: 0 ok; 1 the demo hook raised (the demo script's own status); 10 an
environment error; 64 an existing site that was not to be dropped.
"""

import json
import os
import re
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from frappe_nix_tools.app_tools import devshell
from frappe_nix_tools.app_tools.config import ENV, ConfigError, load_demo
from frappe_nix_tools.app_tools.devshell import Compose, Stop, capture, say, warn

USAGE = """\
usage: frappe-demo [--site S] [--fresh] [--recreate-site] [--erpnext-demo | --no-erpnext-demo]
                   [--date YYYY-MM-DD] [--seed N] [--no-up]
A repeatable demo site (docs/app-standards/screenshots.md).
--fresh on a site that exists DROPS ITS DATABASE: outside CI it needs --recreate-site
(which implies --fresh) or a yes at the prompt.
"""

ENVIRONMENT = 10
KEPT = 64

#: The spec's demo block, read by Node, which strips the spec's types.
SPEC_DEMO_JS = """
    const m = await import(process.argv[1]);
    process.stdout.write(JSON.stringify((m.default && m.default.demo) || {}));
  """


class Fail(Exception):
	"""``frappe-demo: <message>`` on stderr, then exit CODE."""

	def __init__(self, message: str, code: int = ENVIRONMENT) -> None:
		super().__init__(message)
		self.code = code


class Help(Exception):
	pass


@dataclass
class DemoArgs:
	# None: not given, so $FRAPPE_SITE.
	site: str | None = None
	fresh: bool = False
	recreate: bool = False
	# None: the spec's or the module's choice.
	erpnext: bool | None = None
	date: str = ""
	seed: str = ""
	up: bool = True


def parse_args(argv: list[str]) -> DemoArgs:
	"""Left to right, as the bash read it: a value is the next word (which may
	be empty, meaning not given); -h stops at once; the first unknown word is an
	error, exit 10."""
	args = DemoArgs()
	i = 0
	while i < len(argv):
		word = argv[i]
		if word in ("--site", "--date", "--seed"):
			if i + 1 >= len(argv):
				raise Fail(f"{word} needs a value")
			setattr(args, word[2:], argv[i + 1])
			i += 2
			continue
		if word == "--fresh":
			args.fresh = True
		elif word == "--recreate-site":
			args.fresh = args.recreate = True
		elif word == "--erpnext-demo":
			args.erpnext = True
		elif word == "--no-erpnext-demo":
			args.erpnext = False
		elif word == "--no-up":
			args.up = False
		elif word in ("-h", "--help"):
			raise Help
		else:
			raise Fail(f"unknown argument {word} (see --help)")
		i += 1
	return args


@dataclass(frozen=True)
class Demo:
	"""The demo's parameters, resolved: the command line, else the spec's demo
	block, else the module's."""

	date: str
	seed: str
	erpnext: bool


def resolve(args: DemoArgs, spec_demo: dict[str, Any], module: dict[str, Any]) -> Demo:
	date = args.date or devshell.jq_raw(devshell.alt(spec_demo.get("date"), module.get("date")))
	seed = args.seed or devshell.jq_raw(devshell.alt(spec_demo.get("seed"), module.get("seed")))
	if args.erpnext is not None:
		erpnext = args.erpnext
	elif "erpnextDemo" in spec_demo:
		erpnext = devshell.truthy(spec_demo["erpnextDemo"])
	else:
		erpnext = devshell.truthy(module.get("erpnext-demo"))
	if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date):
		raise Fail(f"--date must be YYYY-MM-DD, not {date}")
	if not re.fullmatch(r"[0-9]+", seed):
		raise Fail(f"--seed must be a whole number, not {seed}")
	return Demo(date=date, seed=seed, erpnext=erpnext)


def project_name(pyproject: Path) -> str:
	try:
		with pyproject.open("rb") as f:
			name = tomllib.load(f)["project"]["name"]
	except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError):
		raise Fail("cannot read [project].name from pyproject.toml") from None
	if not isinstance(name, str):
		raise Fail("cannot read [project].name from pyproject.toml")
	return name


def bring_up(repo: str, bench: Path, compose: Compose) -> None:
	"""The bench up, as frappe-test's stage 1 brings it: the database, then the
	web server (a process stopped while it is still pending, as provisioning
	stops them, is not started again)."""
	if compose.up():
		say("frappe-demo: the bench is already up")
	else:
		say("frappe-demo: starting the bench (devenv up -D)")
		if devshell.devenv_up(cwd=repo) != 0:
			raise Fail("devenv up -D failed")
	# Counted, not timed, as frappe-shots waits for the web server; one count
	# for both.
	tries = devshell.TRIES
	while not devshell.db_answers():
		tries -= 1
		if tries <= 0:
			raise Fail("the database did not come up within 300 s")
		time.sleep(2)
	while True:
		port = devshell.webserver_port(bench)
		if port and devshell.web_answers(port, devshell.bench_id()):
			break
		tries -= 1
		if tries <= 0:
			holder = devshell.who(port)
			raise Fail(
				"the web server did not answer within 300 s" + (f" (port {port}: {holder})" if holder else "")
			)
		time.sleep(2)


def _object(text: str) -> dict[str, Any] | None:
	try:
		value = json.loads(text)
	except ValueError:
		return None
	return value if isinstance(value, dict) else None


def demo(argv: list[str]) -> int:
	try:
		args = parse_args(argv)
	except Help:
		sys.stdout.write(USAGE)
		return 0
	try:
		config = load_demo()
	except ConfigError as e:
		raise Fail(str(e)) from None
	# Read; the commands frappe-demo starts have configuration of their own.
	os.environ.pop(ENV, None)

	bench_root = os.environ.get("FRAPPE_BENCH_ROOT", "")
	if not bench_root:
		raise Fail("run it in the app's dev shell (nix develop): FRAPPE_BENCH_ROOT is unset")
	bench = Path(bench_root)
	repo = devshell.repo_root()
	if repo is None:
		raise Fail("not in the app's repository: DEVENV_ROOT is unset and git finds no work tree")
	site = args.site if args.site is not None else os.environ.get("FRAPPE_SITE", "")
	if not site:
		raise Fail("no site: pass --site or set FRAPPE_SITE")
	app = project_name(Path(repo) / "pyproject.toml")

	rc, enabled = capture(["frappe-nix", "config", "modules.demo"], cwd=repo)
	if rc != 0:
		raise Fail("cannot read the configuration (frappe-nix config modules.demo)")
	if enabled != "true":
		warn("frappe-demo: notice: the demo module is off for this app; nothing to do")
		return 0
	rc, module_json = capture(["frappe-nix", "config", "--json", "demo"], cwd=repo)
	module = _object(module_json) if rc == 0 else None
	if module is None:
		raise Fail("cannot read the demo module's configuration")

	# The spec's demo block, when the app has a screenshot spec.
	spec_demo: Any = {}
	spec = Path(repo) / "marketplace" / "screenshots.ts"
	if spec.is_file():
		spec_demo = devshell.node_json(SPEC_DEMO_JS, str(spec))
		if not isinstance(spec_demo, dict):
			raise Fail("marketplace/screenshots.ts does not load")
	params = resolve(args, spec_demo, module)

	# --fresh drops the site's database, and the default site is the dev shell's
	# own. In CI that is the point; at a desk it is somebody's work, so an
	# existing site is only dropped when asked (frappe-test's rule).
	site_dir = bench / "sites" / site
	if args.fresh and not args.recreate and os.environ.get("CI") != "true" and site_dir.is_dir():
		if devshell.has_terminal():
			if not devshell.ask_recreate(
				f"frappe-demo: {site} exists. --fresh drops its database and resets Administrator's password."
			):
				raise Fail(f"kept {site}; drop --fresh to add the demo to it, or pass --recreate-site", KEPT)
		else:
			raise Fail(
				f"{site} exists: drop --fresh to add the demo to it, or pass --recreate-site to drop its database and make it again",
				KEPT,
			)
	say(
		f"frappe-demo: {site}, date {params.date}, seed {params.seed}, erpnext demo {'on' if params.erpnext else 'off'}"
	)

	# ── 1. up, and the site ─────────────────────────────────────────────────
	compose = Compose.from_env()
	if args.up:
		bring_up(repo, bench, compose)
	if args.fresh or not site_dir.is_dir():
		say(f"frappe-demo: provisioning {site}")
		# As frappe-test does: with the app processes stopped, so the
		# apps-reconcile task the runtime runs on start does not race
		# provision-site's installs.
		if not devshell.provision(site, compose):
			raise Fail(f"provision-site {site} failed")

	# ── 2-5. the wizard, the demo data, the app's hook, demo.json ───────────
	sites = bench / "sites"
	cmd = [str(bench / "env" / "bin" / "python"), config.demo_py, "--site", site, "--sites-path", str(sites)]
	cmd += ["--app", app, "--date", params.date, "--seed", params.seed, "--demo", module_json]
	cmd += ["--password", devshell.admin_password()]
	if params.erpnext:
		cmd.append("--erpnext-demo")
	try:
		os.chdir(sites)
	except OSError:
		raise Fail(f"no {sites}") from None
	devshell.flush()
	try:
		os.execve(cmd[0], cmd, devshell.faked_env(os.environ))
	except OSError as e:
		warn(f"frappe-demo: {cmd[0]}: {e.strerror}")
		return 127


def main(argv: list[str]) -> int:
	try:
		return demo(argv)
	except Fail as e:
		warn(f"frappe-demo: {e}")
		return e.code
	except Stop as e:
		return e.code
	except KeyboardInterrupt:
		return 130


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))
