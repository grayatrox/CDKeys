import ast
from collections.abc import Iterator

import pytest
import sqlcipher3

from cdkeys.db import DBConn, ensure_schema
from cdkeys.licenses import redacted_preview, upsert_license


@pytest.fixture
def con() -> Iterator[DBConn]:
    c: DBConn = sqlcipher3.connect(":memory:")
    ensure_schema(c)
    yield c
    c.close()


def _row(con: DBConn, license_id: str) -> tuple[object, ...] | None:
    return con.execute(
        "SELECT product_key, serial_number, assigned_device, associated_login,"
        " notes, updated_utc FROM license WHERE id = ?",
        (license_id,),
    ).fetchone()


def test_insert_stores_normalised_values(con: DBConn) -> None:
    lid = upsert_license(
        con,
        product_name="Office",
        product_key="  K1 ",
        serial_number="",
        assigned_device=" laptop ",
        notes="   ",
        now=lambda: "T1",
    )

    assert _row(con, lid) == ("K1", None, "laptop", None, None, "T1")


def test_update_with_missing_values_keeps_stored_ones(con: DBConn) -> None:
    lid = upsert_license(
        con,
        product_name="Office",
        identity="INV-1",
        product_key="K1",
        assigned_device="laptop",
        notes="first",
        now=lambda: "T1",
    )
    again = upsert_license(
        con, product_name="Office", identity="INV-1", now=lambda: "T2"
    )

    assert again == lid
    assert _row(con, lid) == ("K1", None, "laptop", None, "first", "T2")


def test_update_with_new_values_overwrites(con: DBConn) -> None:
    lid = upsert_license(
        con,
        product_name="Office",
        product_key="K1",
        assigned_device="laptop",
        now=lambda: "T1",
    )
    upsert_license(
        con,
        product_name="Office",
        product_key="K1",
        assigned_device="desktop",
        notes="moved",
        now=lambda: "T2",
    )

    assert _row(con, lid) == ("K1", None, "desktop", None, "moved", "T2")
    count = con.execute("SELECT COUNT(*) FROM license").fetchone()
    assert count == (1,)


def _preview(entries: list[dict[str, str]]) -> object:
    return ast.literal_eval(redacted_preview(entries))


def test_preview_redacts_key_and_serial_and_keeps_the_rest() -> None:
    entries = [
        {
            "product_name": "Office",
            "product_key": "SECRET-KEY",
            "serial_number": "SN-9",
            "assigned_device": "laptop",
            "notes": "",
        }
    ]

    assert _preview(entries) == [
        {
            "product_name": "Office",
            "product_key": "REDACTED",
            "serial_number": "REDACTED",
            "assigned_device": "laptop",
        }
    ]


def test_preview_redacts_key_repeated_in_notes() -> None:
    entries = [
        {"product_name": "Office", "product_key": "SECRET", "notes": "was SECRET"}
    ]

    assert _preview(entries) == [
        {"product_name": "Office", "product_key": "REDACTED", "notes": "was REDACTED"}
    ]


def test_preview_does_not_mutate_input() -> None:
    entries = [{"product_name": "Office", "product_key": "SECRET"}]

    redacted_preview(entries)

    assert entries == [{"product_name": "Office", "product_key": "SECRET"}]
