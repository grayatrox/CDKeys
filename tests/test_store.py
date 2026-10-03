from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlcipher3

from cdkeys.db import DBConn, ensure_schema, open_db
from cdkeys.licenses import make_license_id, upsert_license
from cdkeys.store import (
    DuplicateLicenceError,
    IdentityRequiredError,
    InvalidLicenceError,
    LicenceFields,
    LicenceNotFoundError,
    add_licence,
    delete_licence,
    get_licence,
    list_licences,
    update_licence,
)

# The license table as created before the identity column existed (#647).
OLD_SCHEMA = """
CREATE TABLE product (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE license (
    id TEXT PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES product(id),
    product_key TEXT, serial_number TEXT, assigned_device TEXT,
    associated_login TEXT, notes TEXT,
    created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_utc TEXT
);
"""


@pytest.fixture
def con() -> Iterator[DBConn]:
    c: DBConn = sqlcipher3.connect(":memory:")
    ensure_schema(c)
    yield c
    c.close()


def _fields(**kw: str) -> LicenceFields:
    return LicenceFields(**kw)


def _ids(con: DBConn) -> list[str]:
    return [lic.id for lic in list_licences(con)]


# --- schema migration -------------------------------------------------------


def test_old_database_gains_identity_column_and_keeps_rows(tmp_path: Path) -> None:
    db = tmp_path / "old.sqlite3"
    raw = sqlcipher3.connect(db)
    raw.execute("PRAGMA key = 'pw';")
    raw.executescript(OLD_SCHEMA)
    raw.execute("INSERT INTO product(name) VALUES ('Office')")
    raw.execute(
        "INSERT INTO license(id, product_id, product_key) VALUES ('abc', 1, 'K1')"
    )
    raw.commit()
    raw.close()

    con = open_db(db, "pw")
    ensure_schema(con)
    ensure_schema(con)  # idempotent
    con.commit()
    columns = [r[1] for r in con.execute("PRAGMA table_info(license)").fetchall()]
    licence = get_licence(con, "abc")
    con.close()

    assert columns.count("identity") == 1
    assert licence.product_key == "K1"
    assert licence.identity is None


# --- list / search / get ----------------------------------------------------


def test_list_is_sorted_by_product_then_key(con: DBConn) -> None:
    upsert_license(con, product_name="windows", product_key="B")
    upsert_license(con, product_name="Office", product_key="Z")
    upsert_license(con, product_name="Office", product_key="A")

    rows = [(lic.product_name, lic.product_key) for lic in list_licences(con)]

    assert rows == [("Office", "A"), ("Office", "Z"), ("windows", "B")]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", {"Office", "Game", "Tool"}),
        ("off", {"Office"}),
        ("LAPTOP", {"Game"}),
        ("me@x", {"Tool"}),
        ("boxed", {"Game"}),
        ("sn-9", {"Tool"}),
        ("inv-7", {"Game"}),
        ("  ", {"Office", "Game", "Tool"}),
        ("nothing", set()),
    ],
)
def test_search_matches_any_field_ignoring_case(
    con: DBConn, query: str, expected: set[str]
) -> None:
    upsert_license(con, product_name="Office", product_key="AAAA")
    upsert_license(
        con,
        product_name="Game",
        identity="INV-7",
        assigned_device="Laptop",
        notes="Boxed copy",
    )
    upsert_license(
        con, product_name="Tool", serial_number="SN-9", associated_login="me@x.com"
    )

    assert {lic.product_name for lic in list_licences(con, query)} == expected


def test_get_returns_all_fields_including_identity(con: DBConn) -> None:
    lid = upsert_license(
        con,
        product_name="Game",
        identity=" INV-1 ",
        product_key="K",
        notes="n",
        now=lambda: "T1",
    )

    lic = get_licence(con, lid)

    assert (lic.product_name, lic.identity, lic.product_key, lic.notes) == (
        "Game",
        "INV-1",
        "K",
        "n",
    )
    assert lic.updated_utc == "T1"


def test_get_missing_raises(con: DBConn) -> None:
    with pytest.raises(LicenceNotFoundError):
        get_licence(con, "nope")


# --- add ----------------------------------------------------------------------


def test_add_creates_and_returns_id(con: DBConn) -> None:
    lid = add_licence(con, _fields(product_name="Office", product_key="K1"))

    assert lid == make_license_id(
        product_name="Office",
        identity=None,
        product_key="K1",
        serial_number=None,
        associated_login=None,
    )
    assert get_licence(con, lid).product_key == "K1"


def test_add_refuses_an_existing_licence(con: DBConn) -> None:
    add_licence(con, _fields(product_name="Office", product_key="K1"))

    with pytest.raises(DuplicateLicenceError):
        add_licence(con, _fields(product_name="office", product_key=" k1 "))


@pytest.mark.parametrize(
    "fields",
    [
        {"product_name": "  ", "product_key": "K"},
        {"product_name": "Office"},
        {"product_name": "Office", "notes": "only notes"},
    ],
)
def test_add_rejects_invalid_fields(con: DBConn, fields: dict[str, str]) -> None:
    with pytest.raises(InvalidLicenceError):
        add_licence(con, _fields(**fields))

    assert list_licences(con) == []


# --- update -------------------------------------------------------------------


def test_editing_non_identity_fields_keeps_id_and_can_clear(con: DBConn) -> None:
    lid = add_licence(
        con,
        _fields(
            product_name="Office", product_key="K1", assigned_device="pc", notes="x"
        ),
    )

    new_id = update_licence(
        con,
        lid,
        _fields(product_name="Office", product_key="K1", assigned_device="laptop"),
        now=lambda: "T9",
    )

    lic = get_licence(con, new_id)
    assert new_id == lid
    assert (lic.assigned_device, lic.notes, lic.updated_utc) == ("laptop", None, "T9")


def test_editing_the_key_rehashes_and_keeps_created_time(con: DBConn) -> None:
    lid = add_licence(con, _fields(product_name="Office", product_key="K1"))
    created = get_licence(con, lid).created_utc

    new_id = update_licence(con, lid, _fields(product_name="Office", product_key="K2"))

    assert new_id != lid
    assert _ids(con) == [new_id]
    assert get_licence(con, new_id).created_utc == created
    # Re-adding the corrected key now finds it instead of duplicating it.
    with pytest.raises(DuplicateLicenceError):
        add_licence(con, _fields(product_name="Office", product_key="K2"))


def test_edit_refuses_to_collide_with_another_licence(con: DBConn) -> None:
    a = add_licence(con, _fields(product_name="Office", product_key="K1"))
    add_licence(con, _fields(product_name="Office", product_key="K2"))

    with pytest.raises(DuplicateLicenceError):
        update_licence(con, a, _fields(product_name="Office", product_key="K2"))

    assert get_licence(con, a).product_key == "K1"


def test_edit_moving_to_another_product_drops_orphan_product(con: DBConn) -> None:
    lid = add_licence(con, _fields(product_name="Offce", product_key="K1"))

    new_id = update_licence(con, lid, _fields(product_name="Office", product_key="K1"))

    names = [r[0] for r in con.execute("SELECT name FROM product").fetchall()]
    assert get_licence(con, new_id).product_name == "Office"
    assert names == ["Office"]


def test_edit_missing_licence_raises(con: DBConn) -> None:
    with pytest.raises(LicenceNotFoundError):
        update_licence(con, "nope", _fields(product_name="P", product_key="K"))


def test_edit_rejects_invalid_fields(con: DBConn) -> None:
    lid = add_licence(con, _fields(product_name="Office", product_key="K1"))

    with pytest.raises(InvalidLicenceError):
        update_licence(con, lid, _fields(product_name="Office"))


def _legacy_manual_licence(con: DBConn) -> str:
    # A manual-identity licence written before identity was stored.
    lid = make_license_id(
        product_name="Game",
        identity="INV-1",
        product_key=None,
        serial_number=None,
        associated_login=None,
    )
    con.execute("INSERT INTO product(name) VALUES ('Game')")
    con.execute(
        "INSERT INTO license(id, product_id, product_key, notes) "
        "VALUES (?, 1, 'K', 'old')",
        (lid,),
    )
    return lid


def test_legacy_manual_licence_non_identity_edit_keeps_id(con: DBConn) -> None:
    lid = _legacy_manual_licence(con)

    new_id = update_licence(
        con, lid, _fields(product_name="Game", product_key="K", notes="new")
    )

    assert new_id == lid
    assert get_licence(con, lid).notes == "new"


def test_legacy_manual_licence_identity_edit_needs_identity(con: DBConn) -> None:
    lid = _legacy_manual_licence(con)

    with pytest.raises(IdentityRequiredError):
        update_licence(con, lid, _fields(product_name="Game", product_key="K2"))

    new_id = update_licence(
        con, lid, _fields(product_name="Game", product_key="K2", identity="INV-1")
    )
    assert new_id == lid  # same identity, so the same id
    assert get_licence(con, lid).identity == "INV-1"


# --- delete -------------------------------------------------------------------


def test_delete_removes_licence_and_orphan_product(con: DBConn) -> None:
    keep = add_licence(con, _fields(product_name="Office", product_key="K1"))
    gone = add_licence(con, _fields(product_name="Game", product_key="K2"))

    delete_licence(con, gone)

    names = [r[0] for r in con.execute("SELECT name FROM product").fetchall()]
    assert _ids(con) == [keep]
    assert names == ["Office"]


def test_delete_missing_raises(con: DBConn) -> None:
    with pytest.raises(LicenceNotFoundError):
        delete_licence(con, "nope")
