from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from upsert_licenses import prompt_passphrase


def _answers(*values: str) -> Callable[[str], str]:
    it: Iterator[str] = iter(values)
    return lambda _prompt: next(it)


def _unexpected(prompt: str) -> str:
    raise AssertionError(f"unexpected prompt: {prompt}")


def test_existing_db_asks_once_and_does_not_create(tmp_path: Path) -> None:
    db = tmp_path / "keys.sqlite3"
    db.touch()

    result = prompt_passphrase(db, ask_secret=_answers("pw"), ask=_unexpected)

    assert result == ("pw", False)


def test_existing_db_rejects_empty_passphrase(tmp_path: Path) -> None:
    db = tmp_path / "keys.sqlite3"
    db.touch()

    with pytest.raises(SystemExit, match="cannot be empty"):
        prompt_passphrase(db, ask_secret=_answers(""), ask=_unexpected)


def test_missing_db_creates_after_confirmation_and_matching_repeat(
    tmp_path: Path,
) -> None:
    db = tmp_path / "keys.sqlite3"

    result = prompt_passphrase(db, ask_secret=_answers("pw", "pw"), ask=_answers("y"))

    assert result == ("pw", True)


@pytest.mark.parametrize("answer", ["", "n", "no", "nope"])
def test_missing_db_declined_exits(tmp_path: Path, answer: str) -> None:
    db = tmp_path / "keys.sqlite3"

    with pytest.raises(SystemExit, match="Not creating"):
        prompt_passphrase(db, ask_secret=_unexpected, ask=_answers(answer))


def test_missing_db_mismatched_repeat_exits(tmp_path: Path) -> None:
    db = tmp_path / "keys.sqlite3"

    with pytest.raises(SystemExit, match="do not match"):
        prompt_passphrase(db, ask_secret=_answers("pw", "pW"), ask=_answers("yes"))


def test_missing_db_rejects_empty_new_passphrase(tmp_path: Path) -> None:
    db = tmp_path / "keys.sqlite3"

    with pytest.raises(SystemExit, match="cannot be empty"):
        prompt_passphrase(db, ask_secret=_answers(""), ask=_answers("y"))
