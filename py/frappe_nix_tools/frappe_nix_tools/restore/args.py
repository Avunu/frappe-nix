"""``bench restore``'s arguments.

Parsed by hand rather than by click's option parser, because the command is a
front for Frappe's own ``bench restore``: an option it does not know is handed
to that command untouched, and so is everything after ``--``. click owns the
command (see frappe_nix_tools.bench_cli); this owns the words after it.
"""

from dataclasses import dataclass, field

USAGE = """\
Usage: bench restore [<sql-file>] [options]

With no file, fetches the most recent backup from the object store and
restores it into $FRAPPE_SITE, creating the site if it does not exist.

  --list                 Show available backups and exit
  --at <when>            Restore the newest backup at or within <when>, an ISO 8601
                         date or time (2026-10-02, 2026-10-02T03:00), a backup's
                         own name (20261002_030000), or any leading part of either
  --files                Also restore the public files archive
  --private-files        Also restore the private files archive
  --no-cache             Re-download even if the cached copy is intact
  --no-site-config       Do not carry any key from production's site config
  --no-migrate           Skip `bench migrate` afterwards
  --no-force             Fail instead of replacing an existing database
  --neutralize           Pause the scheduler and switch mail off in the restored
                         database (the default for a fetched backup; see
                         frappe-nix.restore.neutralize)
  --no-neutralize        Leave the restored database as production had it
  --encryption-key <k>   Override the backup encryption key

Anything else is passed through to `bench restore`.
"""


class UsageError(Exception):
	"""A command line this command refuses; exit status 1, as the bash version had."""


@dataclass
class RestoreArgs:
	sql_file: str | None = None
	list_only: bool = False
	at: str | None = None
	# None: whatever restore.files says.
	files: bool | None = None
	private_files: bool | None = None
	no_cache: bool = False
	seed_config: bool = True
	migrate: bool | None = None
	force: bool = True
	neutralize: bool | None = None
	encryption_key: str | None = None
	help: bool = False
	passthru: list[str] = field(default_factory=list)


FLAGS = {
	"--list": ("list_only", True),
	"--files": ("files", True),
	"--private-files": ("private_files", True),
	"--no-cache": ("no_cache", True),
	"--no-site-config": ("seed_config", False),
	"--no-migrate": ("migrate", False),
	"--no-force": ("force", False),
	"--neutralize": ("neutralize", True),
	"--no-neutralize": ("neutralize", False),
}
VALUED = {"--at": "at", "--encryption-key": "encryption_key"}


def parse_args(argv: list[str]) -> RestoreArgs:
	"""The arguments, in order: -h/--help stops at once, as do errors."""
	args = RestoreArgs()
	words = list(argv)
	while words:
		word = words.pop(0)
		name, eq, inline = word.partition("=")
		if word in ("-h", "--help"):
			args.help = True
			return args
		if word == "--":
			args.passthru.extend(words)
			break
		if word in FLAGS:
			attr, value = FLAGS[word]
			setattr(args, attr, value)
		elif name in VALUED:
			if eq:
				value = inline
			elif words:
				value = words.pop(0)
			else:
				raise UsageError(f"{name} needs a value")
			setattr(args, VALUED[name], value)
		elif word.startswith("-"):
			args.passthru.append(word)
		elif args.sql_file is not None:
			raise UsageError(f"unexpected argument '{word}'")
		else:
			args.sql_file = word
	return args
