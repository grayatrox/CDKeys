"""GUI startup: find the database (settings or first-run choice) and unlock it."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from cdkeys.db import (
    DBConn,
    WrongPassphraseError,
    backup_db,
    ensure_schema,
    needs_migration,
    open_db,
)
from cdkeys.settings import SettingsError, load_settings, save_db_path

APP_NAME = "CD Key Manager"
DB_FILTER = "SQLCipher database (*.sqlite3 *.db);;All files (*)"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UseDatabase:
    path: Path
    create: bool  # the file does not exist yet


@dataclass(frozen=True)
class ChooseDatabase:
    """No settings file yet: ask the user to open or create a database."""


@dataclass(frozen=True)
class StartupError:
    message: str


Plan = UseDatabase | ChooseDatabase | StartupError


def plan_startup(cli_value: str | None, settings_file: Path) -> Plan:
    """Decide how to start, without any UI.

    An existing but invalid settings file is an error rather than a prompt,
    so the GUI never overwrites a file the user wrote by hand.
    """
    if cli_value is not None and cli_value.strip():
        path = Path(cli_value.strip()).expanduser()
    elif not settings_file.exists():
        return ChooseDatabase()
    else:
        try:
            path = load_settings(settings_file).db_path
        except SettingsError as e:
            return StartupError(str(e))
    return UseDatabase(path=path, create=not path.exists())


def resolve_plan(plan: Plan, settings_file: Path) -> UseDatabase | None:
    """Turn a plan into a database location, asking the user where needed.

    Returns None if the user cancels or startup cannot continue.
    """
    if isinstance(plan, StartupError):
        QMessageBox.critical(None, APP_NAME, plan.message)
        return None
    if isinstance(plan, ChooseDatabase):
        box = QMessageBox()
        box.setWindowTitle(APP_NAME)
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("No licence database is set up yet.")
        box.setInformativeText(
            "Open the database you already have, or create a new encrypted one."
        )
        open_button = box.addButton("Open existing…", QMessageBox.ButtonRole.AcceptRole)
        create_button = box.addButton("Create new…", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is open_button:
            chosen, _ = QFileDialog.getOpenFileName(
                None, "Open licence database", "", DB_FILTER
            )
        elif clicked is create_button:
            chosen, _ = QFileDialog.getSaveFileName(
                None, "Create licence database", "cd_keys.sqlite3", DB_FILTER
            )
        else:
            return None
        if not chosen:
            return None
        save_db_path(settings_file, Path(chosen))
        return UseDatabase(path=Path(chosen), create=not Path(chosen).exists())
    if plan.create:
        answer = QMessageBox.question(
            None,
            APP_NAME,
            f"No database found at:\n{plan.path}\n\n"
            "Create a new encrypted database there?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return None
    return plan


class PassphraseDialog(QDialog):
    """Masked passphrase entry; asks twice when creating a database."""

    def __init__(
        self, parent: QWidget | None, db_path: Path, confirm: bool, error: str = ""
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("New database" if confirm else "Unlock database")
        self.setMinimumWidth(420)
        self.confirm = confirm
        self.passphrase: str | None = None

        path_label = QLabel(str(db_path))
        path_label.setWordWrap(True)
        path_label.setEnabled(False)
        self.first = QLineEdit()
        self.first.setEchoMode(QLineEdit.EchoMode.Password)
        self.second = QLineEdit()
        self.second.setEchoMode(QLineEdit.EchoMode.Password)
        form = QFormLayout()
        form.addRow("Passphrase", self.first)
        if confirm:
            form.addRow("Repeat", self.second)
        self.problem = QLabel(error)
        self.problem.setStyleSheet("color: #c42b1c;")
        self.problem.setVisible(bool(error))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Create" if confirm else "Unlock"
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(path_label)
        layout.addLayout(form)
        layout.addWidget(self.problem)
        layout.addWidget(buttons)
        self.first.setFocus()

    def accept(self) -> None:
        value = self.first.text()
        if not value:
            self._problem("Enter a passphrase.")
            return
        if self.confirm and self.second.text() != value:
            self._problem("The passphrases do not match.")
            return
        self.passphrase = value
        super().accept()

    def _problem(self, text: str) -> None:
        self.problem.setText(text)
        self.problem.show()


AskPassphrase = Callable[[Path, bool, str], str | None]


def ask_passphrase(db_path: Path, confirm: bool, error: str) -> str | None:
    dialog = PassphraseDialog(None, db_path, confirm, error)
    dialog.exec()
    return dialog.passphrase


def unlock(
    target: UseDatabase,
    ask: AskPassphrase = ask_passphrase,
    backup: Callable[[Path], Path] = backup_db,
) -> DBConn | None:
    """Ask for the passphrase until the database opens; None if cancelled.

    An existing database whose schema is out of date is copied with
    ``backup`` before it is migrated; if the copy fails, nothing is migrated
    and the error propagates.
    """
    error = ""
    while True:
        passphrase = ask(target.path, target.create, error)
        if passphrase is None:
            return None
        try:
            con = open_db(target.path, passphrase, create=target.create)
        except WrongPassphraseError:
            error = "Wrong passphrase. Try again."
            continue
        try:
            if not target.create and needs_migration(con):
                copy = backup(target.path)
                log.info("Backed up %s to %s before migrating it", target.path, copy)
        except BaseException:
            con.close()
            raise
        ensure_schema(con)
        con.commit()
        return con
