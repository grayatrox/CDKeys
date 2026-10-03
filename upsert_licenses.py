#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from getpass import getpass
from pathlib import Path
from pprint import pformat

from cdkeys_db import (
    CDKeysDBError,
    DBConn,
    ensure_schema,
    open_db,
    resolve_db_path,
)


# =========================
# Helpers
# =========================
def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def norm(s: str | None) -> str | None:
    """Trim; empty => None."""
    if s is None:
        return None
    s = s.strip()
    return s if s else None


def canon(s: str | None) -> str | None:
    """Normalize for identity comparisons (case-insensitive)."""
    s = norm(s)
    if s is None:
        return None
    return s.casefold()


def make_license_id(
    *,
    product_name: str,
    identity: str | None,
    product_key: str | None,
    serial_number: str | None,
    associated_login: str | None,
) -> str:
    """
    Deterministic SHA-256 hex digest for a license.

    - Includes product_name so identical tokens across different products
      won't collide.
    - Excludes assigned_device and notes (not part of identity).
    - Accepts a manual identity override for weird vendor schemes or missing
      token fields.

    IMPORTANT: Do not change the v1 payload rules once you start using this DB,
    or you'll generate different IDs for the same licenses.
    """
    pname = canon(product_name)
    if not pname:
        raise ValueError("Product name required for identity.")

    manual = canon(identity)
    if manual:
        payload = "\0".join(
            [
                "v1",
                f"product={pname}",
                f"manual={manual}",
            ]
        )
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
    payload = "\0".join(
        [
            "v1",
            f"product={pname}",
            f"pk={pk or ''}",
            f"sn={sn or ''}",
            f"login={al or ''}",
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_or_create_product_id(con: DBConn, product_name: str) -> int:
    product_name = product_name.strip()
    if not product_name:
        raise ValueError("Product name cannot be empty.")

    # Match with canon(), the same normalisation make_license_id uses, so names
    # differing only by case map to one product. SQLite's NOCASE folds ASCII
    # only, so the comparison is done here. The first spelling seen is kept;
    # lowest id wins if an older DB already holds case-variant duplicates.
    wanted = canon(product_name)
    rows = con.execute("SELECT id, name FROM product ORDER BY id").fetchall()
    for product_id, name in rows:
        if canon(name) == wanted:
            return int(product_id)

    con.execute("INSERT INTO product(name) VALUES (?)", (product_name,))
    row = con.execute(
        "SELECT id FROM product WHERE name = ?", (product_name,)
    ).fetchone()
    if row is None:
        raise RuntimeError("Failed to load product after insert.")
    return int(row[0])


def upsert_license(
    con: DBConn,
    *,
    product_name: str,
    identity: str | None = None,  # manual override (optional)
    product_key: str | None = None,
    serial_number: str | None = None,
    assigned_device: str | None = None,  # not part of identity
    associated_login: str | None = None,
    notes: str | None = None,
    now: Callable[[], str] = utc_now_iso,
) -> str:
    """
    Insert/update a license record using hash-as-primary-key.
    Returns the license id (sha256 hex). ``now`` supplies updated_utc.
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
            id, product_id, product_key, serial_number, assigned_device,
            associated_login, notes, updated_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            product_id       = excluded.product_id,
            product_key      = COALESCE(excluded.product_key, license.product_key),
            serial_number    = COALESCE(excluded.serial_number,
                                        license.serial_number),
            associated_login = COALESCE(excluded.associated_login,
                                        license.associated_login),
            assigned_device  = COALESCE(excluded.assigned_device,
                                        license.assigned_device),
            notes            = COALESCE(excluded.notes, license.notes),
            updated_utc      = excluded.updated_utc
        """,
        (
            license_id,
            product_id,
            product_key,
            serial_number,
            assigned_device,
            associated_login,
            notes,
            now(),
        ),
    )
    return license_id


def prompt_passphrase(
    db_path: Path,
    ask_secret: Callable[[str], str] = getpass,
    ask: Callable[[str], str] = input,
) -> tuple[str, bool]:
    """
    Ask for the passphrase; returns (passphrase, create).

    A missing database is only created after explicit confirmation, and a new
    passphrase must be typed twice: a typo there would lock the new database.
    Raises SystemExit if the user declines, or on empty/mismatched input.
    """
    if db_path.exists():
        passphrase = ask_secret("DB passphrase (won't echo): ")
        if not passphrase:
            raise SystemExit("Passphrase cannot be empty.")
        return passphrase, False

    answer = ask(f"Database not found at {db_path}. Create a new one? [y/N] ")
    if answer.strip().casefold() not in ("y", "yes"):
        raise SystemExit("Not creating a database. Check --db / CDKEYS_DB.")
    passphrase = ask_secret("New DB passphrase (won't echo): ")
    if not passphrase:
        raise SystemExit("Passphrase cannot be empty.")
    if ask_secret("Repeat new DB passphrase: ") != passphrase:
        raise SystemExit("Passphrases do not match.")
    return passphrase, True


ENTRY_FIELDS = frozenset(
    {
        "identity",
        "product_name",
        "product_key",
        "serial_number",
        "assigned_device",
        "associated_login",
        "notes",
    }
)


class EntriesError(ValueError):
    """The entries file is unreadable or malformed."""


def load_entries(path: Path) -> list[dict[str, str]]:
    """
    Read and validate licence entries from a JSON file.

    The file holds a list of objects using the keyword arguments of
    upsert_license (see entries.example.json). Everything is validated before
    the database is touched, so a typo cannot leave a half-applied batch.
    Raises EntriesError naming the offending entry (1-based).
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise EntriesError(f"{path}: cannot read: {e}") from e
    except json.JSONDecodeError as e:
        raise EntriesError(f"{path}: not valid JSON: {e}") from e

    if not isinstance(data, list):
        raise EntriesError(f"{path}: must be a JSON list of entries")
    if not data:
        raise EntriesError(f"{path}: no entries")

    entries: list[dict[str, str]] = []
    for i, raw in enumerate(data, start=1):
        if not isinstance(raw, dict):
            raise EntriesError(f"{path}: entry {i}: must be an object")
        unknown = sorted(set(raw) - ENTRY_FIELDS)
        if unknown:
            raise EntriesError(
                f"{path}: entry {i}: unknown field(s): {', '.join(unknown)}"
            )
        for key, value in raw.items():
            if not isinstance(value, str):
                raise EntriesError(f"{path}: entry {i}: '{key}' must be a string")
        if not norm(raw.get("product_name")):
            raise EntriesError(f"{path}: entry {i}: missing 'product_name'")
        entries.append(raw)
    return entries


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Insert or update licences from a JSON file "
        "(see entries.example.json). Keep that file out of git."
    )
    parser.add_argument("entries", type=Path, help="path to the entries JSON file")
    parser.add_argument("--db", help="database path (default: $CDKEYS_DB)")
    args = parser.parse_args(argv)

    try:
        db_path = resolve_db_path(args.db, os.environ)
    except CDKeysDBError as err:
        raise SystemExit(str(err)) from err

    try:
        entries = load_entries(args.entries)
    except EntriesError as err:
        raise SystemExit(str(err)) from err

    from sqlcipher3 import dbapi2 as sqlcipher

    with closing(sqlcipher.connect(":memory:")) as mem:
        print(f"Version: {mem.execute('PRAGMA cipher_version;').fetchone()}")

    passphrase, create = prompt_passphrase(db_path)
    try:
        con = open_db(db_path, passphrase, create=create)
    except CDKeysDBError as err:
        raise SystemExit(str(err)) from err

    print(redacted_preview(entries))

    processed = 0
    try:
        ensure_schema(con)
        for e in entries:
            lid = upsert_license(
                con,
                product_name=e["product_name"],
                identity=e.get("identity"),
                product_key=e.get("product_key"),
                serial_number=e.get("serial_number"),
                assigned_device=e.get("assigned_device"),
                associated_login=e.get("associated_login"),
                notes=e.get("notes"),
            )
            processed += 1
            print(
                f"Upserted ({processed}/{len(entries)}) "
                f"product='{e['product_name']}' id={lid}"
            )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    print(f"Done. Upserted {processed} records into {db_path}")


def redacted_preview(entries: list[dict[str, str]]) -> str:
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
        cleaned = {k: v for k, v in d.items() if v not in ("", None)}

        redacted.append(cleaned)

    return pformat(redacted, width=120, sort_dicts=False)


if __name__ == "__main__":
    main()
