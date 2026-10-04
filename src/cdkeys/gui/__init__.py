"""Desktop key manager (Qt).

Started by double-clicking "CD Key Manager.pyw", or ``cdkeys-gui`` /
``python -m cdkeys.gui`` / ``cdkeys gui``.
"""

from __future__ import annotations

import argparse
import os
import sys
from contextlib import closing

from cdkeys.gui.errors import (
    ShowMessage,
    configure_logging,
    log_path,
    make_excepthook,
    show_message_box,
)
from cdkeys.settings import settings_path


def main(
    argv: list[str] | None = None,
    prog: str | None = None,
    show: ShowMessage = show_message_box,
) -> int:
    """Open the key manager; returns the process exit code.

    Unhandled errors are logged and shown in a message box (see
    cdkeys.gui.errors), as there is usually no console to print them to.
    """
    parser = argparse.ArgumentParser(
        prog=prog, description="Open the CD key manager window."
    )
    parser.add_argument(
        "--db", help="database path (default: db_path in the settings file)"
    )
    args = parser.parse_args(argv)

    # Imported here so the CLI subcommands never need Qt.
    from PySide6.QtWidgets import QApplication

    from cdkeys.gui.app import KeyManagerWindow
    from cdkeys.gui.startup import APP_NAME, plan_startup, resolve_plan, unlock

    log_file = log_path(os.environ)
    configure_logging(log_file)
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName(APP_NAME)
    # Installed for the life of the process: Qt routes errors in slots here,
    # and so does Python for anything that escapes main(), exiting with 1.
    sys.excepthook = make_excepthook(log_file, show)

    settings_file = settings_path(os.environ)
    target = resolve_plan(plan_startup(args.db, settings_file), settings_file)
    if target is None:
        return 0
    con = unlock(target)
    if con is None:
        return 0
    with closing(con):
        window = KeyManagerWindow(con, target.path)
        window.show()
        app.exec()
    return 0
