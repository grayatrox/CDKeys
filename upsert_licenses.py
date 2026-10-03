#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from getpass import getpass
from pprint import pformat

from cdkeys_db import DB_PATH, DBConn, ensure_schema, open_db


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

    con.execute("INSERT OR IGNORE INTO product(name) VALUES (?)", (product_name,))
    row = con.execute(
        "SELECT id FROM product WHERE name = ?", (product_name,)
    ).fetchone()
    if row is None:
        raise RuntimeError("Failed to load product after insert/ignore.")
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

    con = open_db(DB_PATH, passphrase)
    ensure_schema(con)

    # ---- MANUAL ENTRY ZONE ----
    # Notes:
    # - One dict per license record.
    # - Uniqueness is the hash of:
    #   product_name + (product_key/serial_number/associated_login),
    #   OR product_name + identity (manual override).
    # - If you have NO product_key/serial_number/associated_login,
    #   you MUST set "identity".
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
    entries: list[dict[str, str]] = [
        {
            "identity": "invoice_no",  # optional unless no key/serial/login
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
            lid = upsert_license(con, **e)
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

    print(f"Done. Upserted {processed} records into {DB_PATH}")


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
