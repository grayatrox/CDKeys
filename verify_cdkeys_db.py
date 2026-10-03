#!/usr/bin/env python3

from pathlib import Path
from getpass import getpass
import sqlcipher3
import sqlite3
import sys

db_path = Path(r"C:\Users\chris\OneDrive\cd_keys_encrypted.sqlite3")

def open_sqlcipher(db_path: Path, passphrase: str):

    con = sqlcipher3.connect(db_path)

    cursor = con.cursor()

    cursor.execute(f"PRAGMA key = '{passphrase}';")

    try:
        cursor.execute("SELECT count(*) FROM sqlite_master;")
        print("Database opened successfully")
    except sqlcipher3.DatabaseError:
        print("Incorrect key")

    return con

def test_counts(con):
    print("\n--- License Count ---")
    cur = con.execute("SELECT COUNT(*) FROM license;")
    total = cur.fetchone()[0]
    print(f"Total licenses: {total}")

    print("\n--- Licenses Per Product ---")
    cur = con.execute("""
        SELECT p.name, COUNT(*)
        FROM license l
        JOIN product p ON p.id = l.product_id
        GROUP BY p.name
        ORDER BY p.name;
    """)

    for name, count in cur:
        print(f"{name} → {count}")


def test_plaintext_access(db_path: Path):
    print("\n--- Plain sqlite3 test (should FAIL) ---")
    try:
        sqlite3.connect(str(db_path)).execute(
            "SELECT COUNT(*) FROM license;"
        ).fetchall()
        print("⚠ WARNING: Opened with plain sqlite3. This is NOT encrypted.")
    except Exception as e:
        print("OK: Plain sqlite3 cannot read DB.")
        print(f"Error: {e}")


def main():


    if not db_path.exists():
        print("DB file does not exist.")
        sys.exit(1)

    password = getpass("Enter SQLCipher passphrase: ")

    try:
        con = open_sqlcipher(db_path, password)
        test_counts(con)
        con.close()
    except Exception as e:
        print("\nFAILED to open with SQLCipher.")
        print(e)
        sys.exit(1)

    test_plaintext_access(db_path)



if __name__ == "__main__":
    main()