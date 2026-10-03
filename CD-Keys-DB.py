#!/usr/bin/env python3
from __future__ import annotations

import sys
import hashlib
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path
from typing import Optional, Protocol
import sqlcipher3

# =========================
# Config
# =========================
DB_PATH = Path(r"C:\Users\chris\OneDrive\cd_keys_encrypted.sqlite3")

# For a brand new DB you can usually leave this as None.
# If you later need to open a DB created with a specific SQLCipher major version,
# you might set this (commonly 3 or 4 depending on environment).
CIPHER_COMPATIBILITY: Optional[int] = None  # e.g. 4

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
--   product name + (product_key / serial_number / associated_login) OR manual identity override.
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


# =========================
# Types
# =========================
class DBConn(Protocol):
    def execute(self, sql: str, params: tuple = ...) -> object: ...
    def executescript(self, sql: str) -> object: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


# =========================
# Helpers
# =========================
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def norm(s: Optional[str]) -> Optional[str]:
    """Trim; empty => None."""
    if s is None:
        return None
    s = s.strip()
    return s if s else None


def canon(s: Optional[str]) -> Optional[str]:
    """Normalize for identity comparisons (case-insensitive)."""
    s = norm(s)
    if s is None:
        return None
    return s.casefold()


def make_license_id(
    *,
    product_name: str,
    identity: Optional[str],
    product_key: Optional[str],
    serial_number: Optional[str],
    associated_login: Optional[str],
) -> str:
    """
    Deterministic SHA-256 hex digest for a license.

    - Includes product_name so identical tokens across different products won't collide.
    - Excludes assigned_device and notes (not part of identity).
    - Accepts a manual identity override for weird vendor schemes or missing token fields.

    IMPORTANT: Do not change the v1 payload rules once you start using this DB,
    or you'll generate different IDs for the same licenses.
    """
    pname = canon(product_name)
    if not pname:
        raise ValueError("Product name required for identity.")

    manual = canon(identity)
    if manual:
        payload = "\0".join([
            "v1",
            f"product={pname}",
            f"manual={manual}",
        ])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    pk = canon(product_key)
    sn = canon(serial_number)
    al = canon(associated_login)

    if not (pk or sn or al):
        raise ValueError(
            "Cannot auto-generate identity: provide at least one of "
            "product_key / serial_number / associated_login OR set identity manually."
        )

    # Use NUL-separated format + key=value pairs to avoid ambiguity.
    payload = "\0".join([
        "v1",
        f"product={pname}",
        f"pk={pk or ''}",
        f"sn={sn or ''}",
        f"login={al or ''}",
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def connect_sqlcipher(db_path: Path, passphrase: str) -> DBConn:

    con = sqlcipher3.connect(db_path)

    cursor = con.cursor()

    cursor.execute(f"PRAGMA key = '{passphrase}';")

    try:
        cursor.execute("SELECT count(*) FROM sqlite_master;")
        print("Database opened successfully")
    except sqlcipher3.DatabaseError:
        print("Incorrect key")

    # Create schema inside encrypted DB
    con.executescript(SCHEMA_SQL)
    return con


def get_or_create_product_id(con: DBConn, product_name: str) -> int:
    product_name = product_name.strip()
    if not product_name:
        raise ValueError("Product name cannot be empty.")

    con.execute("INSERT OR IGNORE INTO product(name) VALUES (?)", (product_name,))
    row = con.execute("SELECT id FROM product WHERE name = ?", (product_name,)).fetchone()
    assert row is not None, "Failed to load product after insert/ignore."
    return int(row[0])


def upsert_license(
    con: DBConn,
    *,
    product_name: str,
    identity: Optional[str] = None,          # manual override (optional)
    product_key: Optional[str] = None,
    serial_number: Optional[str] = None,
    assigned_device: Optional[str] = None,   # not part of identity
    associated_login: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """
    Insert/update a license record using hash-as-primary-key.
    Returns the license id (sha256 hex).
    """
    product_id = get_or_create_product_id(con, product_name)

    product_key = norm(product_key)
    serial_number = norm(serial_number)
    assigned_device = norm(assigned_device)
    associated_login = norm(associated_login)
    notes = norm(notes)

    license_id = make_license_id(
        product_name=product_name,
        identity=identity,
        product_key=product_key,
        serial_number=serial_number,
        associated_login=associated_login,
    )

    # UPSERT policy:
    # - Only overwrite stored values if the new value is non-NULL.
    # - updated_utc always updates.
    con.execute(
        """
        INSERT INTO license (
            id, product_id, product_key, serial_number, assigned_device, associated_login, notes, updated_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            product_id        = excluded.product_id,
            product_key       = COALESCE(excluded.product_key, license.product_key),
            serial_number     = COALESCE(excluded.serial_number, license.serial_number),
            associated_login  = COALESCE(excluded.associated_login, license.associated_login),
            assigned_device   = COALESCE(excluded.assigned_device, license.assigned_device),
            notes             = COALESCE(excluded.notes, license.notes),
            updated_utc       = excluded.updated_utc
        """,
        (
            license_id,
            product_id,
            product_key,
            serial_number,
            assigned_device,
            associated_login,
            notes,
            utc_now_iso(),
        ),
    )
    return license_id


def main() -> None:

    from sqlcipher3 import dbapi2 as sqlcipher

    con = sqlcipher.connect(":memory:")
    cur = con.execute("PRAGMA cipher_version;")
    print(f"Version: {cur.fetchone()}")


    passphrase = getpass("DB passphrase (won't echo): ")
    if not passphrase:
        raise SystemExit("Passphrase cannot be empty.")

    con = connect_sqlcipher(DB_PATH, passphrase)

    # ---- MANUAL ENTRY ZONE ----
    # Notes:
    # - One dict per license record.
    # - Uniqueness is the hash of: product_name + (product_key/serial_number/associated_login),
    #   OR product_name + identity (manual override).
    # - If you have NO product_key/serial_number/associated_login, you MUST set "identity".
    # - assigned_device and notes are NOT part of uniqueness; change them any time.
    #
    # Template (copy/paste one block per entry):
    # {
    #     "identity": "invoice_no",          # optional unless no key/serial/login
    #     "product_name": "Product",
    #     "product_key": "key",
    #     "serial_number": "SN",
    #     "assigned_device": "device_name",
    #     "associated_login": "email_username",
    #     "notes": "notes",
    # },
    entries = [
        {
            "identity": "invoice_no",          # optional unless no key/serial/login
            "product_name": "Product",
            "product_key": "key",
            "serial_number": "SN",
            "assigned_device": "device_name",
            "associated_login": "email_username",
            "notes": "notes",
        },
        
    ]
    # ---------------------------

    print(redacted_preview(entries))



    processed = 0
    try:
        for e in entries:
            lid = upsert_license(con, **e)  # type: ignore[arg-type]
            processed += 1
            print(f"Upserted ({processed}/{len(entries)}) product='{e['product_name']}' id={lid}")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    print(f"Done. Upserted {processed} records into {DB_PATH}")
import sys
from pprint import pformat

def redacted_preview(entries: list[dict]) -> str:
    redacted = []

    for e in entries:
        d = dict(e)  # shallow copy

        key = d.get("product_key")
        serial = d.get("serial_number")
        notes = d.get("notes")

        # Redact key field
        if key:
            d["product_key"] = "REDACTED"

        # Redact serial field
        if serial:
            d["serial_number"] = "REDACTED"

        # Replace literal key inside notes
        if key and notes and key in notes:
            d["notes"] = notes.replace(key, "REDACTED")

        # Remove empty / None fields
        cleaned = {
            k: v
            for k, v in d.items()
            if v not in ("", None)
        }

        redacted.append(cleaned)

    return pformat(redacted, width=120, sort_dicts=False)



if __name__ == "__main__":
    main()