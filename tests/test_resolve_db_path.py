from pathlib import Path

import pytest

from cdkeys_db import ConfigError, resolve_db_path


def test_cli_value_wins_over_environment() -> None:
    result = resolve_db_path("cli.sqlite3", {"CDKEYS_DB": "env.sqlite3"})

    assert result == Path("cli.sqlite3")


def test_environment_used_without_cli_value() -> None:
    result = resolve_db_path(None, {"CDKEYS_DB": "env.sqlite3"})

    assert result == Path("env.sqlite3")


def test_home_is_expanded() -> None:
    result = resolve_db_path("~/keys.sqlite3", {})

    assert result == Path.home() / "keys.sqlite3"


@pytest.mark.parametrize(
    ("cli", "environ"),
    [
        (None, {}),
        (None, {"CDKEYS_DB": "   "}),
        ("", {}),
    ],
)
def test_missing_or_blank_raises(cli: str | None, environ: dict[str, str]) -> None:
    with pytest.raises(ConfigError, match=r"--db.*CDKEYS_DB"):
        resolve_db_path(cli, environ)
