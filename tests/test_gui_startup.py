import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import pytest

from cdkeys.db import DBConn, ensure_schema, open_db
from cdkeys.gui import startup
from cdkeys.gui.startup import (
    ChooseDatabase,
    StartupError,
    UseDatabase,
    plan_startup,
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


class _ScriptedDialog:
    """Stands in for PassphraseDialog, returning queued answers."""

    answers: list[str | None] = []
    confirms: list[bool] = []

    def __init__(self, _parent: object, _path: Path, confirm: bool) -> None:
        _ScriptedDialog.confirms.append(confirm)
        self.passphrase = _ScriptedDialog.answers.pop(0)


def _unlock_with(
    monkeypatch: pytest.MonkeyPatch, target: UseDatabase, answers: list[str | None]
) -> tuple[DBConn | None, list[str]]:
    errors: list[str] = []
    _ScriptedDialog.answers = list(answers)
    _ScriptedDialog.confirms = []
    monkeypatch.setattr(startup, "PassphraseDialog", _ScriptedDialog)
    monkeypatch.setattr(
        messagebox, "showerror", lambda _t, msg, **_k: errors.append(msg)
    )
    return startup.unlock(tk.Misc(), target), errors


def _existing_db(path: Path, passphrase: str) -> None:
    # Write the schema: an empty SQLCipher file opens with any passphrase.
    con = open_db(path, passphrase, create=True)
    ensure_schema(con)
    con.commit()
    con.close()


def test_unlock_retries_after_wrong_passphrase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "keys.sqlite3"
    _existing_db(db, "right")

    con, errors = _unlock_with(
        monkeypatch, UseDatabase(db, create=False), ["wrong", "right"]
    )

    assert con is not None
    con.close()
    assert errors == ["Wrong passphrase. Try again."]
    assert _ScriptedDialog.confirms == [False, False]


def test_unlock_cancel_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "keys.sqlite3"
    _existing_db(db, "right")

    con, _ = _unlock_with(monkeypatch, UseDatabase(db, create=False), [None])

    assert con is None


def test_unlock_creates_new_db_with_confirmation_and_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "new.sqlite3"

    con, _ = _unlock_with(monkeypatch, UseDatabase(db, create=True), ["pw"])

    assert con is not None
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master").fetchall()}
    con.close()
    assert _ScriptedDialog.confirms == [True]
    assert {"product", "license"} <= tables
