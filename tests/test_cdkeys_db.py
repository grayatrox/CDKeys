import sqlite3
from pathlib import Path

import pytest

from cdkeys_db import ensure_schema, open_db


def test_new_db_reopens_with_same_passphrase(tmp_path: Path) -> None:
    db = tmp_path / "keys.sqlite3"
    con = open_db(db, "correct horse")
    ensure_schema(con)
    con.execute("INSERT INTO product(name) VALUES (?)", ("Office",))
    con.commit()
    con.close()

    con = open_db(db, "correct horse")
    rows = con.execute("SELECT name FROM product").fetchall()
    con.close()

    assert rows == [("Office",)]


def test_db_is_not_readable_as_plain_sqlite(tmp_path: Path) -> None:
    db = tmp_path / "keys.sqlite3"
    con = open_db(db, "correct horse")
    ensure_schema(con)
    con.commit()
    con.close()

    plain = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not a database"):
            plain.execute("SELECT count(*) FROM sqlite_master").fetchall()
    finally:
        plain.close()
