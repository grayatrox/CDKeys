from collections.abc import Iterator

import pytest
import sqlcipher3

from cdkeys.db import DBConn, ensure_schema
from cdkeys.licenses import upsert_license
from cdkeys.verify import case_duplicate_products


@pytest.fixture
def con() -> Iterator[DBConn]:
    c: DBConn = sqlcipher3.connect(":memory:")
    ensure_schema(c)
    yield c
    c.close()


def _products(con: DBConn) -> list[tuple[int, str]]:
    rows = con.execute("SELECT id, name FROM product").fetchall()
    return [(r[0], r[1]) for r in rows]


@pytest.mark.parametrize("second", ["office", " OFFICE ", "Straße", "STRASSE"])
def test_product_name_differing_only_by_case_reuses_product(
    con: DBConn, second: str
) -> None:
    first = "Straße" if second in ("Straße", "STRASSE") else "Office"
    lid1 = upsert_license(con, product_name=first, product_key="ABC")
    lid2 = upsert_license(con, product_name=second, product_key="ABC")

    products = _products(con)
    assert lid1 == lid2
    assert len(products) == 1
    assert products[0][1] == first
    row = con.execute("SELECT product_id FROM license WHERE id = ?", (lid1,)).fetchone()
    assert row == (products[0][0],)


def test_case_duplicate_report_finds_legacy_duplicates(con: DBConn) -> None:
    # Rows as the pre-#621 code could leave them.
    for name in ["Office", "Windows", "office", "OFFICE"]:
        con.execute("INSERT INTO product(name) VALUES (?)", (name,))

    assert case_duplicate_products(con) == [["Office", "office", "OFFICE"]]


def test_case_duplicate_report_empty_when_clean(con: DBConn) -> None:
    upsert_license(con, product_name="Office", product_key="ABC")
    upsert_license(con, product_name="office", product_key="DEF")

    assert case_duplicate_products(con) == []


def test_distinct_products_get_distinct_rows(con: DBConn) -> None:
    upsert_license(con, product_name="Office", product_key="ABC")
    upsert_license(con, product_name="Windows", product_key="ABC")

    assert [name for _, name in _products(con)] == ["Office", "Windows"]
