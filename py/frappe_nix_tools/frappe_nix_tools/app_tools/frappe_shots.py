"""frappe-shots: repeatable screenshots of the app's demo site (docs/app-standards/spec.md §5.5).

  frappe-shots [--spec marketplace/screenshots.ts] [--only a,b] [--theme light|dark]
               [--update | --check] [--reuse-site | --recreate-site] [--out docs/screenshots]
               [--video <name>]

Run by lib/standards/tools/frappe-shots.nix, with chromium, libwebp, ffmpeg, Node
24, the fixed fonts and lib/shots/ (its node_modules built by Nix) on hand, inside
an opted-in app's dev shell.

Unless --reuse-site: frappe-demo --fresh builds the demo site (bringing the bench
up when it is down), with its demo script on libfaketime's clock from the demo day
at 09:00 in the screenshots' timezone (a start time: the clock advances, so
timeouts still fire), then `bench build`. A bench this run started is stopped
again at the end. The site is $FRAPPE_SITE, recreated: outside CI (CI=true), an
existing site is only dropped with --recreate-site or a yes at the prompt, as
frappe-test does.

Then lib/shots/runner.ts drives chromium over the site: --update (the default)
writes the lossless WebPs and docs/screenshots/manifest.json; --check writes diff
PNGs under .dev-dist/shots/diff/ only. Masters go to .dev-dist/shots/; --video
<name> records one of the spec's flows to .dev-dist/shots/<name>.mp4 (never
committed).

Exit codes: 0 no differences, or updated; 1 differences under --check; 2 a spec
error; 3 an environment error; 4 a capture error (navigation, timeout or a page
error); 64 an existing site that was not to be dropped.
"""

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from frappe_nix_tools.app_tools import devshell
from frappe_nix_tools.app_tools.config import ENV, ConfigError, load_shots
from frappe_nix_tools.app_tools.devshell import Compose, Stop, call, capture, say, warn

USAGE = """\
usage: frappe-shots [--spec marketplace/screenshots.ts] [--only a,b] [--theme light|dark]
                    [--update | --check] [--reuse-site | --recreate-site] [--out docs/screenshots]
                    [--video <name>]
Repeatable screenshots of the demo site (docs/app-standards/screenshots.md).
Unless --reuse-site, $FRAPPE_SITE is recreated for the demo: that DROPS ITS DATABASE.
Outside CI an existing site needs --recreate-site (or a yes at the prompt).
"""

SPEC_ERROR = 2
ENVIRONMENT = 3
KEPT = 64

#: The spec's demo block and timezone, read by Node, which strips the spec's types.
SPEC_JS = """
  const m = await import(process.argv[1]);
  const s = m.default || {};
  process.stdout.write(JSON.stringify({ demo: s.demo || {}, timezone: s.timezone ?? null }));
"""


class Fail(Exception):
	"""``frappe-shots: <message>`` on stderr, then exit CODE."""

	def __init__(self, code: int, message: str) -> None:
		super().__init__(message)
		self.code = code


class Help(Exception):
	pass


@dataclass
class ShotsArgs:
	spec: str = ""
	only: str = ""
	theme: str = ""
	mode: str = "update"
	reuse: bool = False
	recreate: bool = False
	out: str = ""
	video: str = ""


VALUED = ("--spec", "--only", "--theme", "--out", "--video")


def parse_args(argv: list[str]) -> ShotsArgs:
	"""Left to right, as the bash read it: a value is the next word (which may
	be empty, meaning not given); -h stops at once; the first unknown word is a
	usage error, exit 2. The last of --update and --check wins."""
	args = ShotsArgs()
	i = 0
	while i < len(argv):
		word = argv[i]
		if word in VALUED:
			if i + 1 >= len(argv):
				raise Fail(SPEC_ERROR, f"{word} needs a value")
			setattr(args, word[2:], argv[i + 1])
			i += 2
			continue
		if word == "--update":
			args.mode = "update"
		elif word == "--check":
			args.mode = "check"
		elif word == "--reuse-site":
			args.reuse = True
		elif word == "--recreate-site":
			args.recreate = True
		elif word in ("-h", "--help"):
			raise Help
		else:
			raise Fail(SPEC_ERROR, f"unknown argument {word} (see --help)")
		i += 1
	return args


@dataclass(frozen=True)
class Demo:
	"""What the demo is made from: the spec's demo block over the demo module's,
	and the zone the screenshots are set in (the spec's, else the module's;
	runner.ts reads the same)."""

	zone: str
	date: str
	seed: str
	erpnext: bool


def resolve(spec: dict[str, Any], shots_cfg: dict[str, Any], demo_cfg: dict[str, Any]) -> Demo:
	spec_demo = spec.get("demo")
	spec_demo = spec_demo if isinstance(spec_demo, dict) else {}
	erpnext = spec_demo["erpnextDemo"] if "erpnextDemo" in spec_demo else demo_cfg.get("erpnext-demo")
	return Demo(
		zone=devshell.jq_raw(devshell.alt(spec.get("timezone"), shots_cfg.get("timezone"))),
		date=devshell.jq_raw(devshell.alt(spec_demo.get("date"), demo_cfg.get("date"))),
		seed=devshell.jq_raw(devshell.alt(spec_demo.get("seed"), demo_cfg.get("seed"))),
		erpnext=devshell.jq_raw(erpnext) == "true",
	)


def demo_flags(site: str, demo: Demo) -> list[str]:
	flags = ["--fresh", "--recreate-site", "--site", site, "--date", demo.date, "--seed", demo.seed]
	return [*flags, "--erpnext-demo" if demo.erpnext else "--no-erpnext-demo"]


def runner_args(
	args: ShotsArgs, *, spec: str, out: str, repo: str, site: str, port: str, cfg: dict[str, Any], date: str
) -> list[str]:
	cmd = ["--spec", spec, "--base", f"http://{site}:{port}", "--mode", args.mode, "--out", out]
	cmd += ["--masters", f"{repo}/.dev-dist/shots"]
	cmd += [
		"--timezone",
		devshell.jq_raw(cfg.get("timezone")),
		"--locale",
		devshell.jq_raw(cfg.get("locale")),
	]
	cmd += ["--clock", date, "--password", devshell.admin_password()]
	if args.only:
		cmd += ["--only", args.only]
	if args.theme:
		cmd += ["--theme", args.theme]
	if args.video:
		cmd += ["--video", args.video]
	return cmd


def config_json(*key: str) -> dict[str, Any] | None:
	rc, out = capture(["frappe-nix", "config", "--json", *key])
	if rc != 0:
		return None
	try:
		value = json.loads(out)
	except ValueError:
		return None
	return value if isinstance(value, dict) else None


class Shots:
	def __init__(self) -> None:
		self.compose = Compose.from_env()
		# A bench this run started, to stop again at the end.
		self.started = False

	def run(self, argv: list[str]) -> int:
		try:
			args = parse_args(argv)
		except Help:
			sys.stdout.write(USAGE)
			return 0
		try:
			config = load_shots()
		except ConfigError as e:
			raise Fail(ENVIRONMENT, str(e)) from None
		# Read; the commands frappe-shots starts have configuration of their own.
		os.environ.pop(ENV, None)

		bench_root = os.environ.get("FRAPPE_BENCH_ROOT", "")
		if not bench_root:
			raise Fail(ENVIRONMENT, "run it in the app's dev shell (nix develop): FRAPPE_BENCH_ROOT is unset")
		bench = Path(bench_root)
		repo = devshell.repo_root()
		if repo is None:
			raise Fail(
				ENVIRONMENT, "not in the app's repository: DEVENV_ROOT is unset and git finds no work tree"
			)
		site = os.environ.get("FRAPPE_SITE", "")
		if not site:
			raise Fail(ENVIRONMENT, "FRAPPE_SITE is unset")
		try:
			os.chdir(repo)
		except OSError:
			raise Fail(ENVIRONMENT, f"no {repo}") from None
		if args.reuse and args.recreate:
			raise Fail(SPEC_ERROR, "--reuse-site and --recreate-site exclude each other")

		rc, enabled = capture(["frappe-nix", "config", "modules.screenshots"])
		if rc != 0:
			raise Fail(SPEC_ERROR, "cannot read the configuration (frappe-nix config modules.screenshots)")
		if enabled != "true":
			warn("frappe-shots: notice: the screenshots module is off for this app; nothing to do")
			return 0
		# Relative to the repository, whatever the working directory was.
		spec_path = os.path.realpath(args.spec or "marketplace/screenshots.ts")
		out = os.path.realpath(args.out or "docs/screenshots")
		if not os.path.isfile(spec_path):
			raise Fail(SPEC_ERROR, f"no screenshot spec at {spec_path}")
		shots_cfg = config_json("screenshots")
		if shots_cfg is None:
			raise Fail(SPEC_ERROR, "cannot read the screenshots module's configuration")
		demo_cfg = config_json("demo")
		if demo_cfg is None:
			raise Fail(SPEC_ERROR, "cannot read the demo module's configuration")
		spec = devshell.node_json(SPEC_JS, spec_path)
		if not isinstance(spec, dict):
			raise Fail(SPEC_ERROR, f"{spec_path} does not load")
		demo = resolve(spec, shots_cfg, demo_cfg)

		if not args.reuse:
			self.make_demo(args, config.shots_dir, config.faketime_lib, bench, site, demo)
		elif not self.compose.up():
			raise Fail(ENVIRONMENT, "--reuse-site, but the bench is not up (frappe-demo brings it up)")

		config_file = bench / "sites" / "common_site_config.json"
		port = devshell.webserver_port(bench)
		if not port:
			where = devshell.ports_file() or config_file
			raise Fail(ENVIRONMENT, f"no webserver_port in {where}")
		tries = devshell.TRIES
		while not devshell.web_answers(port, devshell.bench_id()):
			tries -= 1
			if tries <= 0:
				holder = devshell.who(port)
				raise Fail(
					ENVIRONMENT,
					f"the web server did not answer on port {port} within 300 s"
					+ (f" ({holder})" if holder else ""),
				)
			time.sleep(2)

		runner = runner_args(
			args, spec=spec_path, out=out, repo=repo, site=site, port=port, cfg=shots_cfg, date=demo.date
		)
		return call(["node", f"{config.shots_dir}/runner.ts", *runner])

	def make_demo(
		self, args: ShotsArgs, shots_dir: str, faketime_lib: str, bench: Path, site: str, demo: Demo
	) -> None:
		# The site is recreated, which drops its database, and it is the dev
		# shell's own. In CI that is the point; at a desk it is somebody's work
		# (frappe-test's rule, spec Appendix I).
		if not args.recreate and os.environ.get("CI") != "true" and (bench / "sites" / site).is_dir():
			if devshell.has_terminal():
				if not devshell.ask_recreate(
					f"frappe-shots: {site} exists. The demo recreates it, which drops its database and resets Administrator's password."
				):
					raise Fail(
						KEPT, f"kept {site}; pass --reuse-site to photograph it as it is, or --recreate-site"
					)
			else:
				raise Fail(
					KEPT,
					f"{site} exists: pass --reuse-site to photograph it as it is, or --recreate-site to drop its database and make the demo again",
				)
		# frappe-demo's demo script runs on libfaketime's clock, from 09:00 on the
		# demo day in the screenshots' timezone, given in UTC (frappe-demo runs it
		# with TZ=UTC; it reads these two), so every record it makes is dated on
		# that day, and the gap to the page's clock (noon, runner.ts) is the same
		# on every machine. The bench's own processes stay on the real clock:
		# libfaketime deadlocks redis (jemalloc) and, through its process-shared
		# semaphore, the bench's CLI.
		rc, start = capture(["node", f"{shots_dir}/clock.ts", demo.date, "09:00:00", demo.zone])
		if rc != 0:
			raise Fail(SPEC_ERROR, f"no demo clock for {demo.date} in {demo.zone}")
		say(
			f"frappe-shots: the demo data is made from {demo.date} 09:00 {demo.zone} ({start} UTC, libfaketime)"
		)
		faked = {
			**os.environ,
			"FRAPPE_NIX_SHOTS_PRELOAD": faketime_lib,
			"FRAPPE_NIX_SHOTS_FAKETIME": f"@{start}",
		}
		self.started = not self.compose.up()
		if call(["frappe-demo", *demo_flags(site, demo)], env=faked) != 0:
			raise Fail(ENVIRONMENT, "frappe-demo failed")
		if call(["bench", "build"], cwd=bench) != 0:
			raise Fail(ENVIRONMENT, "bench build failed")

	def stop(self) -> None:
		if self.started and self.compose.up():
			self.compose.down()


def main(argv: list[str]) -> int:
	shots = Shots()
	try:
		return shots.run(argv)
	except Fail as e:
		warn(f"frappe-shots: {e}")
		return e.code
	except Stop as e:
		return e.code
	except KeyboardInterrupt:
		return 130
	finally:
		shots.stop()


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))
