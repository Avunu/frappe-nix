"""``frappe-init``'s command line.

Parsed by hand rather than by click or argparse, because the rules are the
documented ones (docs/reference/scaffolder.md) and neither parser can say them:

-   a usage error is exit 1, except on a line with ``--sync`` or ``--check``
    anywhere, where it is 2 (1 is drift there), and where every flag is read
    before the first unknown one is reported;
-   ``--commit`` takes its message only as ``--commit=<msg>``, so
    ``--commit <dir>`` is the flag and a target;
-   the app standards flags are accepted here and refused later, once the mode
    is known, outside app mode.

``--frappe-version`` and ``--site`` are the names ``--version`` and
``--default-site`` had before CONVENTIONS.md (Scaffolder: version flag;
Identity options). They are still read, with a warning, through 1.x, until frappe-nix 2.0.0, and are not in the help.
"""

from dataclasses import dataclass, field

USAGE = """\
Usage: frappe-init [options] [target-dir]

Scaffold a new frappe-nix bench, migrate an existing `bench init` bench in
place, or put a dev environment in a single Frappe app's own repository. The
mode is detected from the target directory (default: the current directory when
it is a bench or a Frappe app, otherwise a new bench).

Mode:
  --sync                   Write this app's managed files (frappe-nix sync --write);
                           the app must have opted in, or pass --standards
  --check                  Report drift in them and change nothing (frappe-nix sync --check)
  --init                   Force scaffold mode
  --migrate                Force migration mode (also re-syncs a frappe-nix bench)
  --app                    Force app mode: this repo is one Frappe app, and the
                           bench around it is generated from flake inputs
  --force                  Relax the mode guard; never overwrites an existing file
  --dry-run                Print the plan and exit without changing anything
  -y, --yes                Assume yes (required to migrate in a non-TTY)

Common:
  --version <v>            develop | version-16 | version-15  (overrides detection)
  --name <name>            Bench name (default: existing / target dir basename)
  --default-site <site>    Default site (default: existing default_site)
  --skip-lock              Do not run `uv lock`
  -h, --help               Show this help

App standards (app mode; see https://github.com/Avunu/frappe-nix/tree/main/docs/app-standards):
  --standards <profile>    Opt in: minimal | recommended | recommended@<minor> |
                           github:<owner>/<repo>[/<ref>] | ./<dir>  (with --app or --sync)
  --profile-path <dir>     Read the org profile from a local checkout (profile authors)
  --only <path,…>          Limit to these managed files
  --init-listing           --sync: also seed marketplace/listing.toml
  --format <f>             --check: text | json | github
  --expect-rev <sha>       --check: the frappe-nix revision frappe-nix-tools came from
  (--force with --sync replaces an unmanaged flake.nix or .envrc on first opt-in)

Scaffold only:
  --apps <a,b,c>           Apps to add (names, owner/repo, or git URLs)

Migration only:
  --vendor <a,b>           Force these apps to be vendored (source committed here)
  --no-vendor              Abort instead of vendoring an app with no usable remote
  --allow-file-remotes     Accept filesystem paths as submodule URLs
  --legacy-apps <policy>   shim | skip | abort, for apps with only a setup.py
  --strict                 Treat dirty / unpushed apps as errors
  --absorb-gitdirs         Run `git submodule absorbgitdirs` after registering
  --keep-db-root-password  Do not blank mariadb_root_password before committing
  --commit[=<msg>]         Commit the result instead of only staging it

With a terminal and no flags you are prompted for what is missing.
"""

DEFAULT_COMMIT_MSG = "chore: migrate bench to frappe-nix"

#: The old name of a flag → its name now (CONVENTIONS.md, Scaffolder).
RENAMED = {"--frappe-version": "--version", "--site": "--default-site"}

#: Flags that take a value, as `--flag <v>` or `--flag=<v>`, and the field each sets.
VALUED = {
	"--version": "version",
	"--frappe-version": "version",
	"--apps": "apps_csv",
	"--name": "name",
	"--default-site": "site",
	"--site": "site",
	"--vendor": "force_vendor",
	"--legacy-apps": "legacy_apps",
	"--standards": "standards",
}
#: `frappe-nix sync`'s own flags, passed through as given.
SYNC_VALUED = ("--only", "--format", "--expect-rev", "--profile-path")
#: --check's own flags: unknown without --sync or --check.
CHECK_ONLY = ("--format", "--expect-rev")
#: The app standards flags, refused once the mode is known to be another.
APP_FLAGS = ("--standards", "--only", "--profile-path", "--init-listing")

#: Switches and the field each sets to True.
SWITCHES = {
	"--force": "force",
	"--dry-run": "dry_run",
	"-y": "assume_yes",
	"--yes": "assume_yes",
	"--skip-lock": "skip_lock",
	"--strict": "strict",
	"--no-vendor": "no_vendor",
	"--allow-file-remotes": "allow_file_remotes",
	"--absorb-gitdirs": "absorb_gitdirs",
	"--keep-db-root-password": "keep_db_root_password",
}


class UsageError(Exception):
	"""A command line frappe-init refuses: the usage, then MESSAGE, exit CODE."""

	def __init__(self, message: str, code: int, *, show_usage: bool = True) -> None:
		super().__init__(message)
		self.code = code
		self.show_usage = show_usage


@dataclass
class InitArgs:
	# auto | init | migrate | app
	mode: str = "auto"
	# sync | check, or "" for neither
	app_action: str = ""
	force: bool = False
	dry_run: bool = False
	assume_yes: bool = False
	skip_lock: bool = False
	commit: bool = False
	commit_msg: str = DEFAULT_COMMIT_MSG
	strict: bool = False
	no_vendor: bool = False
	allow_file_remotes: bool = False
	absorb_gitdirs: bool = False
	keep_db_root_password: bool = False
	# "" = auto: shim when vendored, skip when a submodule.
	legacy_apps: str = ""
	# Comma-separated app names.
	force_vendor: str = ""
	version: str = ""
	apps_csv: str = ""
	name: str = ""
	site: str = ""
	target: str = ""
	standards: str = ""
	# --only, --format, --expect-rev, --profile-path, --init-listing: for frappe-nix sync.
	sync_args: list[str] = field(default_factory=list)
	# The first app standards flag on the line, for refusing it outside app mode.
	app_flag: str = ""
	help: bool = False
	# Old flag names that were used, for the warning (RENAMED).
	renamed: list[str] = field(default_factory=list)


def parse_args(argv: list[str]) -> InitArgs:
	args = InitArgs()
	has_action = any(word in ("--sync", "--check") for word in argv)
	usage_code = 2 if has_action else 1
	unknown = ""
	words = list(argv)
	while words:
		word = words.pop(0)
		flag, eq, inline = word.partition("=")
		if not word.startswith("-"):
			args.target = word
			continue
		# A value flag at the end of the line is a usage error.
		if not eq and (word in VALUED or word in SYNC_VALUED) and not words:
			raise UsageError(f"{word} needs a value", usage_code, show_usage=False)
		if flag in CHECK_ONLY and not has_action:
			raise UsageError(f"unknown flag: {word}", 1)
		if flag in APP_FLAGS and not args.app_flag:
			args.app_flag = flag
		if flag in VALUED:
			value = inline if eq else words.pop(0)
			setattr(args, VALUED[flag], value)
			if flag in RENAMED and flag not in args.renamed:
				args.renamed.append(flag)
		elif flag in SYNC_VALUED:
			args.sync_args.extend([word] if eq else [word, words.pop(0)])
		elif word == "--init-listing":
			args.sync_args.append(word)
		elif word == "--commit":
			args.commit = True
		elif flag == "--commit" and eq:
			args.commit = True
			args.commit_msg = inline
		elif word in ("--sync", "--check"):
			args.mode = "app"
			args.app_action = word[2:]
		elif word in ("--init", "--migrate", "--app"):
			args.mode = word[2:]
		elif word in SWITCHES:
			setattr(args, SWITCHES[word], True)
		elif word in ("-h", "--help"):
			args.help = True
			return args
		elif not has_action:
			raise UsageError(f"unknown flag: {word}", 1)
		elif not unknown:
			unknown = word
	if unknown:
		raise UsageError(f"unknown flag: {unknown}", 2)
	return args
