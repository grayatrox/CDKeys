import json
from pathlib import Path

import pytest

from upsert_licenses import EntriesError, load_entries

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "entries.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_loads_valid_entries(tmp_path: Path) -> None:
    data = [
        {"product_name": "Office", "product_key": "AAAA-BBBB"},
        {"product_name": "Game", "identity": "INV-1", "notes": "boxed"},
    ]

    assert load_entries(_write(tmp_path, data)) == data


def test_example_file_is_valid() -> None:
    entries = load_entries(REPO_ROOT / "entries.example.json")

    assert len(entries) >= 1


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"product_name": "Office"}, "must be a JSON list"),
        ([], "no entries"),
        (["Office"], r"entry 1: must be an object"),
        ([{"product_key": "K"}], r"entry 1: missing 'product_name'"),
        ([{"product_name": "  "}], r"entry 1: missing 'product_name'"),
        (
            [{"product_name": "Office"}, {"product_name": "X", "prodct_key": "K"}],
            r"entry 2: unknown field\(s\): prodct_key",
        ),
        ([{"product_name": "Office", "notes": 5}], r"entry 1: 'notes' must be"),
    ],
)
def test_rejects_malformed_entries(tmp_path: Path, data: object, message: str) -> None:
    with pytest.raises(EntriesError, match=message):
        load_entries(_write(tmp_path, data))


def test_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "entries.json"
    path.write_text("[{", encoding="utf-8")

    with pytest.raises(EntriesError, match="not valid JSON"):
        load_entries(path)


def test_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(EntriesError, match="cannot read"):
        load_entries(tmp_path / "nope.json")
