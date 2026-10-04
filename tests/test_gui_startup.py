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
from cdkeys.licenses import upsert_license
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


# --- backup before migrating (#653) -------------------------------------------


def _pre_groups_db(path: Path, passphrase: str) -> list[tuple[str, str]]:
    """An encrypted DB in the pre-groups layout; returns its (id, product) rows."""
    con = open_db(path, passphrase, create=True)
    ensure_schema(con)
    upsert_license(con, product_name="Office", product_key="AAAAA")
    upsert_license(con, product_name="Game", serial_number="SN-1")
    con.execute("DROP TABLE product_group_member")
    con.execute("DROP TABLE product_group")
    con.commit()
    rows = con.execute(
        "SELECT l.id, p.name FROM license l JOIN product p ON p.id = l.product_id "
        "ORDER BY l.id"
    ).fetchall()
    con.close()
    return [(str(i), str(n)) for i, n in rows]


class RecordingBackup:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.copies: list[tuple[Path, bytes]] = []

    def __call__(self, path: Path) -> Path:
        if self.fail:
            raise PermissionError("read-only folder")
        self.copies.append((path, path.read_bytes()))
        return path.with_name(path.name + ".bak")


def test_unlock_backs_up_old_db_before_migrating(tmp_path: Path) -> None:
    db = tmp_path / "keys.sqlite3"
    before = _pre_groups_db(db, "pw")
    original = db.read_bytes()
    backup = RecordingBackup()

    con = unlock(
        UseDatabase(db, create=False), ask=ScriptedPrompt(["pw"]), backup=backup
    )

    assert con is not None
    assert backup.copies == [(db, original)]  # taken before any change
    rows = con.execute(
        "SELECT l.id, p.name FROM license l JOIN product p ON p.id = l.product_id "
        "ORDER BY l.id"
    ).fetchall()
    grouped = con.execute("SELECT count(*) FROM product_group_member").fetchone()
    con.close()
    assert [(str(i), str(n)) for i, n in rows] == before  # ids unchanged
    assert grouped == (0,)  # every product starts ungrouped


def test_unlock_real_backup_lands_next_to_the_db(tmp_path: Path) -> None:
    db = tmp_path / "keys.sqlite3"
    _pre_groups_db(db, "pw")
    original = db.read_bytes()

    con = unlock(UseDatabase(db, create=False), ask=ScriptedPrompt(["pw"]))

    assert con is not None
    con.close()
    backups = list(tmp_path.glob("keys.sqlite3.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original


def test_unlock_does_not_migrate_when_backup_fails(tmp_path: Path) -> None:
    db = tmp_path / "keys.sqlite3"
    _pre_groups_db(db, "pw")
    original = db.read_bytes()

    with pytest.raises(PermissionError):
        unlock(
            UseDatabase(db, create=False),
            ask=ScriptedPrompt(["pw"]),
            backup=RecordingBackup(fail=True),
        )

    assert db.read_bytes() == original
    db.unlink()  # the connection was closed (Windows blocks deleting it)


def test_unlock_skips_backup_when_schema_is_current(tmp_path: Path) -> None:
    db = tmp_path / "keys.sqlite3"
    _existing_db(db, "pw")
    backup = RecordingBackup()

    con = unlock(
        UseDatabase(db, create=False), ask=ScriptedPrompt(["pw"]), backup=backup
    )

    assert con is not None
    con.close()
    assert backup.copies == []


def test_unlock_skips_backup_for_a_new_db(tmp_path: Path) -> None:
    backup = RecordingBackup()

    con = unlock(
        UseDatabase(tmp_path / "new.sqlite3", create=True),
        ask=ScriptedPrompt(["pw"]),
        backup=backup,
    )

    assert con is not None
    con.close()
    assert backup.copies == []
