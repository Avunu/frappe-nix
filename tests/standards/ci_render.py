"""Render the fixture app's CI callers for every case §7 N4 lints (spec §4.7, §7 N4).

    python ci_render.py <frappe-nix checkout> <out directory>

For each case it copies ``tests/fixtures/standards-app`` into a fresh git repository, sets the
profile and the tables the case needs, runs ``frappe-nix sync --write`` offline (the fixture's
own locks stay), and copies the rendered ``.github/`` to ``<out>/<case>/.github``. The cases:

- ``recommended``: the fixture as committed;
- ``example-org``: the fictitious org profile, every module on;
- ``no-<module>``: example-org with one of typescript, tests, listing, commits, releases or
  dependabot off (releases needs commits, and pilot-assets needs releases, so they go too);
- ``no-schedules``: example-org with every ``ci.schedules`` entry empty;
- ``self-hosted``: example-org with ``ci.runs-on = ["self-hosted", "nixos"]``;
- ``github-app``: example-org with screenshots on and ``ci.github-app`` naming a variable and a
  secret, so release, deps and nightly pass ``APP_ID`` and ``APP_PRIVATE_KEY``;
- ``dependabot-ignore``: example-org with ``dependabot.ignore`` conditions longer than oxfmt's
  printWidth (§2.17);

selftest-ci.yml runs actionlint and zizmor (the fixture's pinned versions) on each.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from frappe_nix_tools import cli

OFF = {
	"no-typescript": "[tool.frappe-nix.typescript]\nenable = false\n",
	"no-tests": "[tool.frappe-nix.tests]\nenable = false\n",
	"no-listing": (
		"[tool.frappe-nix.listing]\nenable = false\n[tool.frappe-nix.readme]\nenable = false\n"
		"[tool.frappe-nix.icons]\nenable = false\n"
	),
	"no-commits": (
		"[tool.frappe-nix.commits]\nenable = false\n[tool.frappe-nix.releases]\nenable = false\n"
		"[tool.frappe-nix.pilot-assets]\nenable = false\n"
	),
	"no-releases": "[tool.frappe-nix.releases]\nenable = false\n[tool.frappe-nix.pilot-assets]\nenable = false\n",
	"no-dependabot": "[tool.frappe-nix.dependabot]\nenable = false\n[tool.frappe-nix.docs-site]\nenable = false\n",
	"no-schedules": (
		'[tool.frappe-nix.ci.schedules]\nci = ""\nrelease = ""\ndeps-sweep = ""\ndeps-relock = ""\nnightly = ""\n'
	),
	"self-hosted": '[tool.frappe-nix.ci]\nruns-on = ["self-hosted", "nixos"]\n',
	"github-app": (
		"[tool.frappe-nix.demo]\nenable = true\n[tool.frappe-nix.screenshots]\nenable = true\n"
		'[tool.frappe-nix.ci.github-app]\napp-id = "ORG_CI_APP_ID"\nprivate-key = "ORG_CI_PRIVATE_KEY"\n'
	),
	"dependabot-ignore": (
		"[tool.frappe-nix.dependabot.ignore]\n"
		'npm = [{ dependency-name = "tailwindcss", versions = [">=4.0.0-alpha.0", ">=5.0.0", ">=6.0.0", ">=7.0.0"],'
		' update-types = ["version-update:semver-major", "version-update:semver-minor", "version-update:semver-patch"] }]\n'
		'nix = [{ dependency-name = "nixpkgs" }]\n'
	),
}


def git(root: Path, *args: str) -> None:
	subprocess.run(
		["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(root), *args],
		check=True,
		capture_output=True,
	)


def sync(root: Path) -> None:
	old = Path.cwd()
	os.chdir(root)
	try:
		code = cli.main(["sync", "--write"])
	finally:
		os.chdir(old)
	if code != 0:
		raise SystemExit(f"sync --write failed in {root} (exit {code})")


def case(checkout: Path, work: Path, out: Path, name: str, profile: bool, table: str) -> None:
	root = work / name
	shutil.copytree(checkout / "tests/fixtures/standards-app", root)
	git(root, "init", "-q", "-b", "develop")
	if profile:
		shutil.copytree(checkout / "tests/fixtures/profiles/example-org", root / ".standards-profile")
		text = (root / "pyproject.toml").read_text()
		text = text.replace('profile = "recommended"', 'profile = "./.standards-profile"', 1)
		(root / "pyproject.toml").write_text(text + table)
	git(root, "add", "-A")
	git(root, "commit", "-qm", name)
	sync(root)
	shutil.copytree(root / ".github", out / name / ".github")
	print(f"rendered {name}: {sorted(p.name for p in (out / name / '.github').rglob('*.yml'))}")


def main() -> int:
	checkout, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
	os.environ["FRAPPE_NIX_OFFLINE"] = "1"
	os.environ["FRAPPE_NIX_ALLOW_SKEW"] = "1"
	work = out / ".work"
	work.mkdir(parents=True, exist_ok=True)
	case(checkout, work, out, "recommended", False, "")
	case(checkout, work, out, "example-org", True, "")
	for name, table in OFF.items():
		case(checkout, work, out, name, True, table)
	shutil.rmtree(work)
	return 0


if __name__ == "__main__":
	sys.exit(main())
