"""Output and prompts.

The lines are the bash version's, escape codes included, so a log reads the
same. Prompts are rich's (gum is gone); rich is imported
only when one is asked, and none is asked without a terminal on both stdin
and stdout.
"""

import sys
from collections.abc import Sequence
from typing import NoReturn


class Die(Exception):
	"""Stop: ``ERROR: <message>`` on stderr, exit CODE."""

	def __init__(self, message: str, code: int = 1) -> None:
		super().__init__(message)
		self.code = code


class Exit(Exception):
	"""Stop silently with CODE: a failing step has already said why (set -e)."""

	def __init__(self, code: int) -> None:
		super().__init__(code)
		self.code = code


def die(message: str, code: int = 1) -> NoReturn:
	raise Die(message, code)


def out(text: str = "") -> None:
	print(text, flush=True)


def step(text: str) -> None:
	out(f"\n\033[1m{text}\033[0m")


def info(text: str) -> None:
	out(f"  {text}")


def err(text: str) -> None:
	sys.stdout.flush()
	print(f"  \033[31m✗\033[0m  {text}", file=sys.stderr, flush=True)


def warn_line(text: str) -> None:
	sys.stdout.flush()
	print(f"  \033[33m⚠\033[0m  {text}", file=sys.stderr, flush=True)


def error_line(text: str) -> None:
	sys.stdout.flush()
	print(f"\033[31mERROR:\033[0m {text}", file=sys.stderr, flush=True)


def has_tty() -> bool:
	return sys.stdin.isatty() and sys.stdout.isatty()


def _console():
	from rich.console import Console

	return Console(highlight=False)


def _numbered(header: str, options: Sequence[tuple[str, str]]) -> None:
	console = _console()
	console.print(f"[bold]{header}[/bold]")
	for i, (_, label) in enumerate(options, 1):
		console.print(f"  {i}) {label}", markup=False)


def _pick(answer: str, options: Sequence[tuple[str, str]]) -> str | None:
	answer = answer.strip()
	if answer.isdigit() and 1 <= int(answer) <= len(options):
		return options[int(answer) - 1][0]
	for value, _ in options:
		if answer == value:
			return value
	return None


def choose(header: str, options: Sequence[tuple[str, str]]) -> str:
	"""One of OPTIONS ((value, label) pairs), by number or by value."""
	from rich.prompt import Prompt

	_numbered(header, options)
	while True:
		answer = Prompt.ask("Choose", default="1", console=_console())
		picked = _pick(answer, options)
		if picked is not None:
			return picked
		_console().print(f"[red]Pick 1 to {len(options)}, or one of the names above.[/red]")


def choose_many(header: str, options: Sequence[tuple[str, str]]) -> list[str]:
	"""Any of OPTIONS, as numbers or values separated by commas or spaces; none is fine."""
	from rich.prompt import Prompt

	_numbered(header, options)
	while True:
		answer = Prompt.ask(
			"Choose (e.g. 1,3; empty for none)", default="", show_default=False, console=_console()
		)
		words = [w for w in answer.replace(",", " ").split() if w]
		picked = [_pick(w, options) for w in words]
		if all(p is not None for p in picked):
			return [p for p in picked if p is not None]
		_console().print("[red]Some of those are not on the list.[/red]")


def ask(header: str, default: str) -> str:
	from rich.prompt import Prompt

	return Prompt.ask(header, default=default, console=_console())


def confirm(question: str) -> bool:
	"""Yes or no; always no without a terminal."""
	if not has_tty():
		return False
	from rich.prompt import Confirm

	return Confirm.ask(question, default=True, console=_console())
