import sqlite3
from pathlib import Path

import pytest
import sqlcipher3

from cdkeys_db import (
    DatabaseNotFoundError,
    WrongPassphraseError,
    ensure_schema,
    open_db,
)


def _create_with_product(db: Path, passphrase: str) -> None:
    con = open_db(db, passphrase, create=True)
    ensure_schema(con)
    con.execute("INSERT INTO product(name) VALUES (?)", ("Office",))
    con.commit()
    con.close()


def _product_names(db: Path, passphrase: str) -> list[tuple[str]]:
    con = open_db(db, passphrase)
    rows = con.execute("SELECT name FROM product").fetchall()
    con.close()
    return rows


@pytest.mark.parametrize(
    "passphrase",
    [
        "it's-secret",
        "x'; DROP TABLE product; --",
        "''",
        'double"quote',
    ],
)
def test_passphrase_with_quotes_round_trips(tmp_path: Path, passphrase: str) -> None:
    db = tmp_path / "keys.sqlite3"
    _create_with_product(db, passphrase)

    assert _product_names(db, passphrase) == [("Office",)]


def test_db_keyed_before_escaping_still_opens(tmp_path: Path) -> None:
    # The live DB was keyed with the old f-string PRAGMA; a quote-free
    # passphrase must derive the same key through open_db.
    db = tmp_path / "keys.sqlite3"
    raw = sqlcipher3.connect(db)
    raw.execute("PRAGMA key = 'plain-passphrase';")
    raw.execute("CREATE TABLE product (id INTEGER PRIMARY KEY, name TEXT)")
    raw.execute("INSERT INTO product(name) VALUES ('Office')")
    raw.commit()
    raw.close()

    assert _product_names(db, "plain-passphrase") == [("Office",)]


def test_new_db_reopens_with_same_passphrase(tmp_path: Path) -> None:
    db = tmp_path / "keys.sqlite3"
    con = open_db(db, "correct horse", create=True)
    ensure_schema(con)
    con.execute("INSERT INTO product(name) VALUES (?)", ("Office",))
    con.commit()
    con.close()

    con = open_db(db, "correct horse")
    rows = con.execute("SELECT name FROM product").fetchall()
    con.close()

    assert rows == [("Office",)]


def test_db_with_explicit_sqlcipher4_settings_opens(tmp_path: Path) -> None:
    # open_db relies on the library defaults matching SQLCipher 4, which the
    # existing DB was created with; this catches a change of defaults.
    db = tmp_path / "keys.sqlite3"
    raw = sqlcipher3.connect(db)
    raw.execute("PRAGMA key = 'pw';")
    raw.execute("PRAGMA cipher_compatibility = 4;")
    raw.execute("CREATE TABLE product (id INTEGER PRIMARY KEY, name TEXT)")
    raw.execute("INSERT INTO product(name) VALUES ('Office')")
    raw.commit()
    raw.close()

    assert _product_names(db, "pw") == [("Office",)]


def test_db_with_other_kdf_iter_does_not_open(tmp_path: Path) -> None:
    # Guards the reason the cipher settings are not configurable: a different
    # kdf_iter derives a different key.
    db = tmp_path / "keys.sqlite3"
    raw = sqlcipher3.connect(db)
    raw.execute("PRAGMA key = 'pw';")
    raw.execute("PRAGMA kdf_iter = 64000;")
    raw.execute("CREATE TABLE product (id INTEGER PRIMARY KEY, name TEXT)")
    raw.commit()
    raw.close()

    with pytest.raises(WrongPassphraseError):
        open_db(db, "pw")


def test_wrong_passphrase_raises(tmp_path: Path) -> None:
    db = tmp_path / "keys.sqlite3"
    _create_with_product(db, "correct horse")

    with pytest.raises(WrongPassphraseError):
        open_db(db, "wrong horse")

    # The failed open must not hold the file (Windows blocks deleting it).
    db.unlink()


def test_missing_db_raises_and_creates_nothing(tmp_path: Path) -> None:
    db = tmp_path / "moved" / "keys.sqlite3"

    with pytest.raises(DatabaseNotFoundError):
        open_db(db, "correct horse")

    assert not db.exists()


def test_create_flag_creates_missing_db(tmp_path: Path) -> None:
    db = tmp_path / "keys.sqlite3"
    _create_with_product(db, "correct horse")

    assert db.exists()
    assert _product_names(db, "correct horse") == [("Office",)]


def test_create_flag_on_existing_db_opens_it(tmp_path: Path) -> None:
    db = tmp_path / "keys.sqlite3"
    _create_with_product(db, "correct horse")

    con = open_db(db, "correct horse", create=True)
    rows = con.execute("SELECT name FROM product").fetchall()
    con.close()

    assert rows == [("Office",)]


def test_db_is_not_readable_as_plain_sqlite(tmp_path: Path) -> None:
    db = tmp_path / "keys.sqlite3"
    con = open_db(db, "correct horse", create=True)
    ensure_schema(con)
    con.commit()
    con.close()

    plain = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not a database"):
            plain.execute("SELECT count(*) FROM sqlite_master").fetchall()
    finally:
        plain.close()
