"""Error reporting for the GUI, which normally runs without a console.

Unhandled errors are shown in a message box and their tracebacks written to a
log file in the per-user config folder (see cdkeys.settings.user_config_dir),
since stderr goes nowhere under pythonw. The log stays out of the project
directory so it never lands in git.
Messages never include licence values: store and database errors name
products and paths only.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from types import TracebackType

from cdkeys.settings import user_config_dir

APP_NAME = "CD Key Manager"
LOG_FILENAME = "cdkeys.log"

logger = logging.getLogger("cdkeys.gui")

ShowMessage = Callable[[str], None]
ExceptHook = Callable[
    [type[BaseException], BaseException, TracebackType | None], object
]


def log_path(environ: Mapping[str, str]) -> Path:
    return user_config_dir(environ) / LOG_FILENAME


def configure_logging(path: Path) -> logging.Handler:
    """Send the cdkeys loggers to ``path`` (appending); returns the handler."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    cdkeys_logger = logging.getLogger("cdkeys")
    cdkeys_logger.addHandler(handler)
    cdkeys_logger.setLevel(logging.INFO)
    return handler


def error_message(exc: BaseException, log_file: Path) -> str:
    return (
        f"Something went wrong:\n{type(exc).__name__}: {exc}\n\n"
        f"Details were written to:\n{log_file}"
    )


def show_message_box(message: str) -> None:
    from PySide6.QtWidgets import QMessageBox

    QMessageBox.critical(None, APP_NAME, message)


def make_excepthook(log_file: Path, show: ShowMessage) -> ExceptHook:
    """An excepthook that logs and shows errors instead of printing them.

    Qt calls sys.excepthook for exceptions raised in slots (button clicks
    and so on) and then carries on, so the window stays usable.
    """

    def hook(
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logger.error("Unhandled error", exc_info=(exc_type, exc, tb))
        show(error_message(exc, log_file))

    return hook
