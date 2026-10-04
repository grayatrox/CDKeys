"""
Licence operations for interactive use (the GUI): list, search, add, edit and
delete. No UI code here; callers own the connection and commit or roll back.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cdkeys.db import DBConn
from cdkeys.licenses import (
    canon,
    get_or_create_product_id,
    make_license_id,
    norm,
    upsert_license,
    utc_now_iso,
)


class LicenceError(Exception):
    """Base class for licence operations that cannot be carried out."""


class LicenceNotFoundError(LicenceError):
    pass


class DuplicateLicenceError(LicenceError):
    pass


class InvalidLicenceError(LicenceError):
    pass


class IdentityRequiredError(LicenceError):
    pass


@dataclass(frozen=True)
class LicenceFields:
    """The user-editable fields of a licence."""

    product_name: str
    product_key: str | None = None
    serial_number: str | None = None
    associated_login: str | None = None
    assigned_device: str | None = None
    notes: str | None = None
    identity: str | None = None


@dataclass(frozen=True)
class Licence:
    id: str
    product_name: str
    product_key: str | None
    serial_number: str | None
    associated_login: str | None
    assigned_device: str | None
    notes: str | None
    identity: str | None
    created_utc: str
    updated_utc: str | None

    @property
    def fields(self) -> LicenceFields:
        return LicenceFields(
            product_name=self.product_name,
            product_key=self.product_key,
            serial_number=self.serial_number,
            associated_login=self.associated_login,
            assigned_device=self.assigned_device,
            notes=self.notes,
            identity=self.identity,
        )


_SELECT = """
    SELECT l.id, p.name, l.product_key, l.serial_number, l.associated_login,
           l.assigned_device, l.notes, l.identity, l.created_utc, l.updated_utc
    FROM license l JOIN product p ON p.id = l.product_id
"""
_SEARCHED = (
    "product_name",
    "product_key",
    "serial_number",
    "associated_login",
    "assigned_device",
    "notes",
    "identity",
)


def list_licences(con: DBConn, query: str = "") -> list[Licence]:
    """All licences, sorted by product then key, optionally filtered.

    ``query`` matches case-insensitively anywhere in any field. Filtering is
    done here rather than with SQL LIKE: the store is small and canon()
    already gives the Unicode-aware case folding used everywhere else.
    """
    licences = [Licence(*row) for row in con.execute(_SELECT).fetchall()]
    needle = canon(query)
    if needle:
        licences = [
            lic
            for lic in licences
            if any(needle in (canon(getattr(lic, f)) or "") for f in _SEARCHED)
        ]
    return sorted(
        licences,
        key=lambda lic: (canon(lic.product_name) or "", canon(lic.product_key) or ""),
    )


def get_licence(con: DBConn, licence_id: str) -> Licence:
    row = con.execute(_SELECT + " WHERE l.id = ?", (licence_id,)).fetchone()
    if row is None:
        raise LicenceNotFoundError(f"No licence with id {licence_id}")
    return Licence(*row)


def _exists(con: DBConn, licence_id: str) -> bool:
    found = con.execute("SELECT 1 FROM license WHERE id = ?", (licence_id,))
    return found.fetchone() is not None


def _product_id_of(con: DBConn, licence_id: str) -> int:
    row = con.execute(
        "SELECT product_id FROM license WHERE id = ?", (licence_id,)
    ).fetchone()
    if row is None:
        raise LicenceNotFoundError(f"No licence with id {licence_id}")
    return int(row[0])


def licence_id_for(fields: LicenceFields) -> str:
    """The id these fields hash to; raises InvalidLicenceError if they cannot."""
    if not norm(fields.product_name):
        raise InvalidLicenceError("Product name is required.")
    try:
        return make_license_id(
            product_name=fields.product_name,
            identity=fields.identity,
            product_key=fields.product_key,
            serial_number=fields.serial_number,
            associated_login=fields.associated_login,
        )
    except ValueError as e:
        raise InvalidLicenceError(
            "Enter a product key, serial number or login, or an identity "
            "(e.g. an invoice number) for licences that have none."
        ) from e


def _copy_groups(con: DBConn, from_product_id: int, to_product_id: int) -> None:
    """Add ``to_product_id`` to every group ``from_product_id`` is in (#655).

    Only adds: the target keeps the groups it already had.
    """
    con.execute(
        """
        INSERT OR IGNORE INTO product_group_member (group_id, product_id)
        SELECT group_id, ? FROM product_group_member WHERE product_id = ?
        """,
        (to_product_id, from_product_id),
    )


def _drop_orphan_products(con: DBConn) -> None:
    con.execute("DELETE FROM product WHERE id NOT IN (SELECT product_id FROM license)")


def add_licence(
    con: DBConn, fields: LicenceFields, now: Callable[[], str] = utc_now_iso
) -> str:
    """Store a new licence; returns its id.

    Unlike the batch import, adding a licence that already exists is refused
    rather than merged, so the user is not surprised by a silent update.
    """
    licence_id = licence_id_for(fields)
    if _exists(con, licence_id):
        raise DuplicateLicenceError(
            f"{norm(fields.product_name)}: this licence is already stored."
        )
    return upsert_license(
        con,
        product_name=fields.product_name,
        identity=fields.identity,
        product_key=fields.product_key,
        serial_number=fields.serial_number,
        assigned_device=fields.assigned_device,
        associated_login=fields.associated_login,
        notes=fields.notes,
        now=now,
    )


def _hashed_tokens(f: LicenceFields | Licence) -> tuple[str | None, ...]:
    """The fields (other than identity) that feed make_license_id."""
    return (
        canon(f.product_name),
        canon(f.product_key),
        canon(f.serial_number),
        canon(f.associated_login),
    )


def _is_legacy_manual(lic: Licence) -> bool:
    """A manual-identity licence stored before identities were kept.

    Its id cannot be reproduced from the stored fields, so it can only be
    re-hashed if the user supplies the identity again.
    """
    if lic.identity is not None:
        return False
    try:
        return licence_id_for(lic.fields) != lic.id
    except InvalidLicenceError:
        return True


def update_licence(
    con: DBConn,
    licence_id: str,
    fields: LicenceFields,
    now: Callable[[], str] = utc_now_iso,
) -> str:
    """Replace a licence's fields; returns its (possibly new) id.

    Fields are set exactly as given, so an edit can clear a field. Changing
    the product, key, serial, login or identity changes the id (it is a hash
    of them); the row is then re-keyed, keeping its created time. Moving it
    to another product adds that product to the old one's groups.

    Raises LicenceNotFoundError, InvalidLicenceError, DuplicateLicenceError
    (the new id belongs to another licence) or IdentityRequiredError.
    """
    current = get_licence(con, licence_id)
    new_id = licence_id_for(fields)

    if _is_legacy_manual(current) and norm(fields.identity) is None:
        if _hashed_tokens(fields) != _hashed_tokens(current):
            raise IdentityRequiredError(
                "This licence was stored with a manual identity that was not "
                "recorded at the time. Enter its identity (e.g. the invoice "
                "number) to change the product, key, serial or login."
            )
        new_id = licence_id

    if new_id != licence_id and _exists(con, new_id):
        raise DuplicateLicenceError(
            f"{norm(fields.product_name)}: another stored licence already has "
            "these details."
        )

    product_id = get_or_create_product_id(con, fields.product_name)
    # Groups belong to products, so a licence moved to another product would
    # otherwise drop out of its groups. Done before the old product can be
    # dropped as an orphan below.
    source_product_id = _product_id_of(con, licence_id)
    if product_id != source_product_id:
        _copy_groups(con, source_product_id, product_id)
    values = (
        new_id,
        product_id,
        norm(fields.product_key),
        norm(fields.serial_number),
        norm(fields.associated_login),
        norm(fields.assigned_device),
        norm(fields.notes),
        norm(fields.identity),
        now(),
    )
    if new_id == licence_id:
        con.execute(
            """
            UPDATE license SET id = ?, product_id = ?, product_key = ?,
                serial_number = ?, associated_login = ?, assigned_device = ?,
                notes = ?, identity = ?, updated_utc = ?
            WHERE id = ?
            """,
            (*values, licence_id),
        )
    else:
        con.execute(
            """
            INSERT INTO license (
                id, product_id, product_key, serial_number, associated_login,
                assigned_device, notes, identity, updated_utc, created_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*values, current.created_utc),
        )
        con.execute("DELETE FROM license WHERE id = ?", (licence_id,))
    _drop_orphan_products(con)
    return new_id


def delete_licence(con: DBConn, licence_id: str) -> None:
    if not _exists(con, licence_id):
        raise LicenceNotFoundError(f"No licence with id {licence_id}")
    con.execute("DELETE FROM license WHERE id = ?", (licence_id,))
    _drop_orphan_products(con)
