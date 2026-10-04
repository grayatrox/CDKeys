"""Shared access to the encrypted (SQLCipher) licence database."""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import sqlcipher3

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
    -- Manual identity override, kept so an edited licence can be re-hashed.
    -- Added after the first release; see ensure_schema for older DBs.
    identity TEXT,

    created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_utc TEXT
);

CREATE INDEX IF NOT EXISTS idx_license_product_id
    ON license(product_id);

CREATE INDEX IF NOT EXISTS idx_license_assigned_device
    ON license(assigned_device);

CREATE INDEX IF NOT EXISTS idx_license_associated_login
    ON license(associated_login);

-- Product groups (#653): named tags on products. A product can be in any
-- number of groups, and a group can sit inside another (parent_id). Names
-- are unique ignoring case; cdkeys.groups also compares them with canon().
CREATE TABLE IF NOT EXISTS product_group (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    parent_id INTEGER REFERENCES product_group(id)
);

-- CASCADE on product matters: the store deletes a product when its last
-- licence goes, and that must not be blocked by (or leave behind) a tag.
CREATE TABLE IF NOT EXISTS product_group_member (
    group_id   INTEGER NOT NULL REFERENCES product_group(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES product(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_product_group_parent_id
    ON product_group(parent_id);

CREATE INDEX IF NOT EXISTS idx_product_group_member_product_id
    ON product_group_member(product_id);
"""

# Every table SCHEMA_SQL creates, so needs_migration cannot drift from it.
SCHEMA_TABLES = frozenset(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA_SQL))


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


def needs_migration(con: DBConn) -> bool:
    """Whether ensure_schema would change this database's schema.

    Used to take a backup before an existing database is migrated.
    """
    rows = con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    tables = {row[0] for row in rows.fetchall()}
    if not SCHEMA_TABLES <= tables:
        return True
    columns = {row[1] for row in con.execute("PRAGMA table_info(license)").fetchall()}
    return "identity" not in columns


def _utc_now() -> datetime:
    return datetime.now(UTC)


def backup_db(db_path: Path, now: Callable[[], datetime] = _utc_now) -> Path:
    """Copy the database file next to itself; returns the copy's path.

    The copy is named ``<file name>.<UTC timestamp>.bak`` so the user can
    restore it by hand by renaming it back. It stays encrypted (a byte copy
    of the file) and ``*.bak`` is git-ignored. An existing file of the same
    name is never overwritten (FileExistsError).
    """
    stamp = now().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = db_path.with_name(f"{db_path.name}.{stamp}.bak")
    with db_path.open("rb") as src, target.open("xb") as dst:
        shutil.copyfileobj(src, dst)
    shutil.copystat(db_path, target)
    return target


def ensure_schema(con: DBConn) -> None:
    """Create the tables and indexes if missing, and migrate older databases.

    CREATE TABLE IF NOT EXISTS never alters an existing table, so columns
    added later are added here. Every migration is additive and nullable.
    """
    con.executescript(SCHEMA_SQL)
    columns = {row[1] for row in con.execute("PRAGMA table_info(license)").fetchall()}
    if "identity" not in columns:
        con.execute("ALTER TABLE license ADD COLUMN identity TEXT")
