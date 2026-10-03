"""Desktop key manager (tkinter): ``cdkeys gui``."""

from __future__ import annotations

import argparse
import os
import tkinter as tk
from contextlib import closing

from cdkeys.settings import settings_path


def main(argv: list[str] | None = None, prog: str | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog=prog, description="Open the CD key manager window."
    )
    parser.add_argument(
        "--db", help="database path (default: db_path in the settings file)"
    )
    args = parser.parse_args(argv)

    # Imported here so the CLI subcommands never need a display.
    from cdkeys.gui.app import KeyManagerApp
    from cdkeys.gui.startup import plan_startup, resolve_plan, unlock

    root = tk.Tk()
    root.withdraw()  # only dialogs until the database is unlocked
    destroyed = False

    def on_destroy(event: tk.Event[tk.Misc]) -> None:
        nonlocal destroyed
        if event.widget is root:
            destroyed = True

    root.bind("<Destroy>", on_destroy, add="+")
    try:
        settings_file = settings_path(os.environ)
        target = resolve_plan(root, plan_startup(args.db, settings_file), settings_file)
        if target is None:
            return
        con = unlock(root, target)
        if con is None:
            return
        with closing(con):
            KeyManagerApp(root, con, target.path)
            root.deiconify()
            root.mainloop()
    finally:
        if not destroyed:  # closing the window already destroyed it
            root.destroy()
