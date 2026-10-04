import pytest

from cdkeys.gui.format import (
    COLUMNS,
    MASK,
    copied_message,
    mask_secret,
    notes_preview,
    row_values,
)
from cdkeys.store import Licence


def _lic(**kw: str | None) -> Licence:
    base: dict[str, str | None] = dict(
        product_key=None,
        serial_number=None,
        associated_login=None,
        assigned_device=None,
        notes=None,
        identity=None,
        updated_utc=None,
    )
    base.update(kw)
    return Licence(
        id="id1",
        product_name="Office",
        created_utc="T0",
        product_key=base["product_key"],
        serial_number=base["serial_number"],
        associated_login=base["associated_login"],
        assigned_device=base["assigned_device"],
        notes=base["notes"],
        identity=base["identity"],
        updated_utc=base["updated_utc"],
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("", ""),
        ("ABCD", MASK * 4),
        ("ABCDEFGH", MASK * 8),
        ("ABCDE-12345-WXYZ", MASK * 6 + "WXYZ"),
        ("A" * 40 + "WXYZ", MASK * 6 + "WXYZ"),  # length not revealed
    ],
)
def test_mask_secret(value: str | None, expected: str) -> None:
    assert mask_secret(value) == expected


@pytest.mark.parametrize(
    ("notes", "expected"),
    [
        (None, ""),
        ("   ", ""),
        ("short", "short"),
        ("first line\nsecond", "first line"),
        ("x" * 50, "x" * 39 + "…"),
    ],
)
def test_notes_preview(notes: str | None, expected: str) -> None:
    assert notes_preview(notes) == expected


def test_row_values_mask_the_key_and_follow_column_order() -> None:
    lic = _lic(
        product_key="AAAAA-BBBBB-CCCCC",
        assigned_device="laptop",
        associated_login="me@x",
        notes="boxed\nmore",
    )

    values = row_values(lic)

    assert len(values) == len(COLUMNS)
    assert values == ("Office", MASK * 6 + "CCCC", "laptop", "me@x", "boxed")
    assert "AAAAA" not in "".join(values)


def test_row_values_fall_back_to_masked_serial() -> None:
    assert row_values(_lic(serial_number="SN-123456789"))[1] == MASK * 6 + "6789"


def test_copied_message_never_contains_the_value() -> None:
    lic = _lic(product_key="SECRET-KEY-VALUE")

    message = copied_message("Product key", lic)

    assert message == "Copied product key for Office to the clipboard."
    assert "SECRET" not in message
