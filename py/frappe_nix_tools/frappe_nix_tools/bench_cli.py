"""``frappe-nix-bench <command>``: the bench subcommands frappe-nix writes in
Python (PLAN.md, Track D).

The dev shell's ``bench`` dispatcher stays bash and hands these over through the
``bench-<command>`` shims, which point ``FRAPPE_NIX_CLI_CONFIG`` at the
configuration Nix generated for the shell (frappe_nix_tools.restore.config).
click is imported when the command line is parsed, and each command's own
module when that command runs, so a command pays only for what it uses.
"""

import sys


def main(argv: list[str] | None = None) -> None:
	import click

	words = list(sys.argv[1:] if argv is None else argv)
	# click drops a `--` that comes straight after the command name, and with it
	# the meaning `bench restore -- <args for Frappe's restore>` has. A second
	# one is what it drops instead.
	if len(words) >= 2 and words[0] == "restore" and words[1] == "--":
		words.insert(1, "--")

	@click.group(context_settings={"help_option_names": ["-h", "--help"]})
	def cli() -> None:
		"""frappe-nix's bench commands. Run them as `bench <command>` in the dev shell."""

	@cli.command(
		"restore",
		add_help_option=False,
		context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False},
	)
	@click.argument("args", nargs=-1, type=click.UNPROCESSED)
	def restore(args: tuple[str, ...]) -> None:
		"""Restore this bench from a backup (bench restore --help)."""
		from frappe_nix_tools.restore.command import main as restore_main

		sys.exit(restore_main(list(args)))

	cli.main(args=words, prog_name="frappe-nix-bench")


if __name__ == "__main__":
	main()
