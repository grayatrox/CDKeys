"""Shared access to the encrypted (SQLCipher) licence database."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import sqlcipher3

DB_PATH = Path(r"C:\Users\chris\OneDrive\cd_keys_encrypted.sqlite3")

# Cipher settings are deliberately left at the SQLCipher 4 defaults (e.g.
# 256000 KDF iterations): the existing DB was created with them, and keying
# with any other kdf_iter/cipher_compatibility would fail to open it.
# Strengthening them needs a PRAGMA rekey / sqlcipher_export migration.


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


class CDKeysDBError(Exception):
    """Base class for errors opening the licence database."""


class DatabaseNotFoundError(CDKeysDBError):
    """The database file does not exist and creation was not requested."""


class WrongPassphraseError(CDKeysDBError):
    """The passphrase does not decrypt the database."""


def _sql_string_literal(value: str) -> str:
    """Quote ``value`` as a SQL string literal, doubling embedded quotes."""
    return "'" + value.replace("'", "''") + "'"


def open_db(db_path: Path, passphrase: str, *, create: bool = False) -> DBConn:
    """Open the SQLCipher database at ``db_path`` keyed with ``passphrase``.

    Raises DatabaseNotFoundError if the file is missing and ``create`` is
    false; sqlcipher3 would otherwise silently create an empty database.
    Raises WrongPassphraseError if the passphrase does not decrypt it.
    """
    if not create and not db_path.exists():
        raise DatabaseNotFoundError(f"Database not found: {db_path}")

    con: DBConn = sqlcipher3.connect(db_path)
    try:
        # PRAGMA cannot take bound parameters, so the passphrase must be quoted
        # here. Quote-free passphrases produce the same literal as before.
        con.execute(f"PRAGMA key = {_sql_string_literal(passphrase)};")

        try:
            # SQLCipher only decrypts on first read, so a wrong key surfaces here.
            con.execute("SELECT count(*) FROM sqlite_master;")
        except sqlcipher3.DatabaseError as e:
            raise WrongPassphraseError(f"Wrong passphrase for {db_path}") from e
    except BaseException:
        con.close()
        raise

    return con


def ensure_schema(con: DBConn) -> None:
    """Create the tables and indexes if they do not already exist."""
    con.executescript(SCHEMA_SQL)
