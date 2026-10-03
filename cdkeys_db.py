"""Shared access to the encrypted (SQLCipher) licence database."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import sqlcipher3

DB_PATH = Path(r"C:\Users\chris\OneDrive\cd_keys_encrypted.sqlite3")

# For a brand new DB you can usually leave this as None.
# If you later need to open a DB created with a specific SQLCipher major version,
# you might set this (commonly 3 or 4 depending on environment).
CIPHER_COMPATIBILITY: int | None = None  # e.g. 4

# KDF iterations: higher = more brute-force resistance, slower unlock.
KDF_ITER = 256000


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS product (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

-- Hash-as-primary-key:
-- license.id is a deterministic SHA-256 hex digest derived from:
--   product name + (product_key / serial_number / associated_login)
--   OR manual identity override.
CREATE TABLE IF NOT EXISTS license (
    id TEXT PRIMARY KEY,

    product_id INTEGER NOT NULL REFERENCES product(id),

    product_key TEXT,
    serial_number TEXT,
    assigned_device TEXT,
    associated_login TEXT,
    notes TEXT,

    created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_utc TEXT
);

CREATE INDEX IF NOT EXISTS idx_license_product_id
    ON license(product_id);

CREATE INDEX IF NOT EXISTS idx_license_assigned_device
    ON license(assigned_device);

CREATE INDEX IF NOT EXISTS idx_license_associated_login
    ON license(associated_login);
"""


class DBCursor(Protocol):
    # Row values are whatever SQLite stored, so they are Any (as in typeshed's
    # sqlite3.Cursor.fetchone).
    def fetchone(self) -> tuple[Any, ...] | None: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...


class DBConn(Protocol):
    def execute(self, sql: str, params: tuple[object, ...] = ...) -> DBCursor: ...
    def executescript(self, sql: str) -> object: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


def _sql_string_literal(value: str) -> str:
    """Quote ``value`` as a SQL string literal, doubling embedded quotes."""
    return "'" + value.replace("'", "''") + "'"


def open_db(db_path: Path, passphrase: str) -> DBConn:
    """Open the SQLCipher database at ``db_path`` keyed with ``passphrase``."""
    con: DBConn = sqlcipher3.connect(db_path)

    # PRAGMA cannot take bound parameters, so the passphrase must be quoted
    # here. Quote-free passphrases produce the same literal as before.
    con.execute(f"PRAGMA key = {_sql_string_literal(passphrase)};")

    try:
        con.execute("SELECT count(*) FROM sqlite_master;")
        print("Database opened successfully")
    except sqlcipher3.DatabaseError:
        print("Incorrect key")

    return con


def ensure_schema(con: DBConn) -> None:
    """Create the tables and indexes if they do not already exist."""
    con.executescript(SCHEMA_SQL)
