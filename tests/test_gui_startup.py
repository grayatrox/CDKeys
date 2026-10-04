from pathlib import Path

import pytest
from PySide6.QtWidgets import QLineEdit

from cdkeys.db import ensure_schema, open_db
from cdkeys.gui.startup import (
    ChooseDatabase,
    PassphraseDialog,
    StartupError,
    UseDatabase,
    plan_startup,
    unlock,
)
from cdkeys.settings import save_db_path


def test_no_settings_file_asks_the_user(tmp_path: Path) -> None:
    assert plan_startup(None, tmp_path / "settings.toml") == ChooseDatabase()


def test_settings_file_pointing_at_existing_db_opens_it(tmp_path: Path) -> None:
    db = tmp_path / "keys.sqlite3"
    db.touch()
    settings_file = tmp_path / "settings.toml"
    save_db_path(settings_file, db)

    assert plan_startup(None, settings_file) == UseDatabase(path=db, create=False)


def test_settings_file_pointing_at_missing_db_plans_creation(tmp_path: Path) -> None:
    db = tmp_path / "new.sqlite3"
    settings_file = tmp_path / "settings.toml"
    save_db_path(settings_file, db)

    assert plan_startup(None, settings_file) == UseDatabase(path=db, create=True)


def test_invalid_settings_file_is_an_error_not_a_prompt(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text("db_path = 5\n", encoding="utf-8")

    plan = plan_startup(None, settings_file)

    assert isinstance(plan, StartupError)
    assert "must be a string" in plan.message


def test_cli_db_wins_and_needs_no_settings_file(tmp_path: Path) -> None:
    db = tmp_path / "keys.sqlite3"
    db.touch()

    plan = plan_startup(str(db), tmp_path / "absent.toml")

    assert plan == UseDatabase(path=db, create=False)


class ScriptedPrompt:
    """Stands in for the passphrase dialog, answering from a queue."""

    def __init__(self, answers: list[str | None]) -> None:
        self.answers = answers
        self.calls: list[tuple[bool, str]] = []  # (confirm, error shown)

    def __call__(self, _path: Path, confirm: bool, error: str) -> str | None:
        self.calls.append((confirm, error))
        return self.answers.pop(0)


def _existing_db(path: Path, passphrase: str) -> None:
    # Write the schema: an empty SQLCipher file opens with any passphrase.
    con = open_db(path, passphrase, create=True)
    ensure_schema(con)
    con.commit()
    con.close()


def test_unlock_retries_and_explains_wrong_passphrase(tmp_path: Path) -> None:
    db = tmp_path / "keys.sqlite3"
    _existing_db(db, "right")
    prompt = ScriptedPrompt(["wrong", "right"])

    con = unlock(UseDatabase(db, create=False), ask=prompt)

    assert con is not None
    con.close()
    assert prompt.calls == [(False, ""), (False, "Wrong passphrase. Try again.")]


def test_unlock_cancel_returns_none(tmp_path: Path) -> None:
    db = tmp_path / "keys.sqlite3"
    _existing_db(db, "right")

    assert unlock(UseDatabase(db, create=False), ask=ScriptedPrompt([None])) is None


def test_unlock_creates_new_db_with_confirmation_and_schema(tmp_path: Path) -> None:
    db = tmp_path / "new.sqlite3"
    prompt = ScriptedPrompt(["pw"])

    con = unlock(UseDatabase(db, create=True), ask=prompt)

    assert con is not None
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master").fetchall()}
    con.close()
    assert prompt.calls == [(True, "")]
    assert {"product", "license"} <= tables


# --- the passphrase dialog itself ---------------------------------------------


@pytest.mark.usefixtures("qapp")
@pytest.mark.parametrize(
    ("confirm", "first", "second", "problem"),
    [
        (False, "", "", "Enter a passphrase."),
        (True, "pw", "pW", "The passphrases do not match."),
    ],
)
def test_passphrase_dialog_refuses_bad_input(
    confirm: bool, first: str, second: str, problem: str
) -> None:
    dialog = PassphraseDialog(None, Path("keys.sqlite3"), confirm)
    dialog.first.setText(first)
    dialog.second.setText(second)

    dialog.accept()

    assert dialog.passphrase is None
    assert dialog.problem.text() == problem
    assert not dialog.problem.isHidden()


@pytest.mark.usefixtures("qapp")
@pytest.mark.parametrize("confirm", [False, True])
def test_passphrase_dialog_accepts_and_masks(confirm: bool) -> None:
    typed = "correct horse"
    dialog = PassphraseDialog(None, Path("keys.sqlite3"), confirm)
    dialog.first.setText(typed)
    dialog.second.setText(typed)

    dialog.accept()

    assert dialog.passphrase == typed
    assert dialog.first.echoMode() == QLineEdit.EchoMode.Password


@pytest.mark.usefixtures("qapp")
def test_passphrase_dialog_shows_a_previous_error() -> None:
    dialog = PassphraseDialog(
        None, Path("keys.sqlite3"), False, "Wrong passphrase. Try again."
    )

    assert dialog.problem.text() == "Wrong passphrase. Try again."
    assert not dialog.problem.isHidden()
