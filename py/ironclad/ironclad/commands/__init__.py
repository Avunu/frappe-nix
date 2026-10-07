"""The ``ironclad`` subcommands. ``ironclad/cli.py`` imports every module here.

The contract for a module is one function::

    def register(subparsers: argparse._SubParsersAction) -> None

which adds one or more subcommands, each with ``set_defaults(func=run)`` where
``run(args: argparse.Namespace) -> int`` returns the exit code (``ironclad.common.report``).
A command raises ``IroncladError`` for a one-line failure; the dispatcher prints it and
exits with its code. Adding a command is adding a module: nothing else is edited.
"""
