#!/usr/bin/env python3

import sqlite3
import sys
from getpass import getpass
from pathlib import Path

import sqlcipher3

from cdkeys_db import DB_PATH, CDKeysDBError, DBConn, open_db


def test_counts(con: DBConn) -> None:
    print("\n--- License Count ---")
    row = con.execute("SELECT COUNT(*) FROM license;").fetchone()
    if row is None:
        raise RuntimeError("COUNT(*) returned no row.")
    print(f"Total licenses: {row[0]}")

    print("\n--- Licenses Per Product ---")
    cur = con.execute("""
        SELECT p.name, COUNT(*)
        FROM license l
        JOIN product p ON p.id = l.product_id
        GROUP BY p.name
        ORDER BY p.name;
    """)

    for name, count in cur.fetchall():
        print(f"{name} → {count}")


def test_plaintext_access(db_path: Path) -> None:
    print("\n--- Plain sqlite3 test (should FAIL) ---")
    try:
        sqlite3.connect(str(db_path)).execute(
            "SELECT COUNT(*) FROM license;"
        ).fetchall()
        print("⚠ WARNING: Opened with plain sqlite3. This is NOT encrypted.")
    except sqlite3.Error as e:
        print("OK: Plain sqlite3 cannot read DB.")
        print(f"Error: {e}")


def main() -> None:

    if not DB_PATH.exists():
        print("DB file does not exist.")
        sys.exit(1)

    password = getpass("Enter SQLCipher passphrase: ")

    try:
        con = open_db(DB_PATH, password)
        test_counts(con)
        con.close()
    except (CDKeysDBError, sqlcipher3.Error) as e:
        print("\nFAILED to open with SQLCipher.")
        print(e)
        sys.exit(1)

    test_plaintext_access(DB_PATH)


if __name__ == "__main__":
    main()
