"""The ``cdkeys`` command: dispatches to the add, verify and gui subcommands."""

from __future__ import annotations

import sys
from collections.abc import Callable

from cdkeys import licenses, verify


def _gui(argv: list[str], prog: str) -> None:
    # Imported on demand so add/verify work on a Python built without tkinter.
    from cdkeys import gui

    gui.main(argv, prog)


COMMANDS: dict[str, tuple[Callable[[list[str], str], None], str]] = {
    "add": (licenses.main, "add or update licences from an entries JSON file"),
    "verify": (verify.main, "check the database opens, is encrypted and is consistent"),
    "gui": (_gui, "open the key manager window"),
}

USAGE = "usage: cdkeys {add,verify,gui} [-h] ...\n\ncommands:\n" + "".join(
    f"  {name:<8}{help_text}\n" for name, (_, help_text) in COMMANDS.items()
)


def main(argv: list[str] | None = None) -> int:
    """Run ``cdkeys <command> [args...]``; returns the process exit code.

    Subcommands report failure by raising SystemExit, as argparse does.
    """
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("-h", "--help"):
        print(USAGE, end="")
        return 0 if args else 2
    if args[0] not in COMMANDS:
        print(f"cdkeys: unknown command {args[0]!r}\n\n{USAGE}", end="")
        return 2
    run, _ = COMMANDS[args[0]]
    run(args[1:], f"cdkeys {args[0]}")
    return 0
