import pytest

from cdkeys.licenses import make_license_id

# Golden digests computed from the original script (commit 17517c8). Existing
# databases key licences by these values; if a test here fails, the v1 payload
# changed and every stored licence id would silently stop matching.
GOLDEN = [
    (
        dict(
            product_name="Microsoft Office 2021",
            product_key="ABCDE-12345-FGHIJ-67890-KLMNO",
        ),
        "798ce7c49181e8b3884319968c1fa2f6b59d6562c177bf99f58c6467dfb7f361",
    ),
    (
        dict(
            product_name="Adobe Acrobat",
            product_key="KEY-1",
            serial_number="SN-1",
            associated_login="me@example.com",
        ),
        "2b17b02f42126bebe17a8a80e295eddfc7894f85b00e6519ba2a728b21e8203d",
    ),
    (
        dict(product_name="Boxed Game", identity="INV-0042", product_key="ignored"),
        "e9ab4f0c0607a873ceec7b959fa286944802369d34caf3d1ea47657294e6e04c",
    ),
]


def _id(**kwargs: str | None) -> str:
    fields: dict[str, str | None] = dict.fromkeys(
        ("identity", "product_key", "serial_number", "associated_login")
    )
    fields.update(kwargs)
    product_name = fields.pop("product_name")
    assert product_name is not None
    return make_license_id(product_name=product_name, **fields)


@pytest.mark.parametrize(("fields", "expected"), GOLDEN)
def test_v1_ids_are_stable(fields: dict[str, str], expected: str) -> None:
    assert _id(**fields) == expected


def test_case_and_whitespace_do_not_change_id() -> None:
    assert _id(product_name="  OFFICE ", product_key=" abc ") == _id(
        product_name="office", product_key="ABC"
    )


def test_manual_identity_overrides_tokens() -> None:
    a = _id(product_name="Game", identity="INV-1", product_key="K1")
    b = _id(product_name="Game", identity="inv-1", serial_number="S2")

    assert a == b


def test_product_name_is_part_of_identity() -> None:
    assert _id(product_name="Office", product_key="K") != _id(
        product_name="Windows", product_key="K"
    )


def test_token_fields_are_not_interchangeable() -> None:
    # A key "X" and a serial "X" are different licences.
    assert _id(product_name="P", product_key="X") != _id(
        product_name="P", serial_number="X"
    )


@pytest.mark.parametrize("product_name", ["", "   "])
def test_blank_product_name_raises(product_name: str) -> None:
    with pytest.raises(ValueError, match="Product name required"):
        _id(product_name=product_name, product_key="K")


def test_no_identity_and_no_tokens_raises() -> None:
    with pytest.raises(ValueError, match="Cannot auto-generate identity"):
        _id(product_name="P", product_key="  ")
