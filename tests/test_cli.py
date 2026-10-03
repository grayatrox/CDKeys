from collections.abc import Callable

import pytest

from cdkeys import cli


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[str], str]]:
    seen: list[tuple[str, list[str], str]] = []

    def fake(name: str) -> Callable[[list[str], str], None]:
        return lambda argv, prog: seen.append((name, argv, prog))

    monkeypatch.setitem(cli.COMMANDS, "add", (fake("add"), "help"))
    monkeypatch.setitem(cli.COMMANDS, "verify", (fake("verify"), "help"))
    return seen


def test_dispatches_subcommand_with_remaining_args(
    calls: list[tuple[str, list[str], str]],
) -> None:
    assert cli.main(["add", "entries.json", "--db", "x.sqlite3"]) == 0
    assert cli.main(["verify"]) == 0

    assert calls == [
        ("add", ["entries.json", "--db", "x.sqlite3"], "cdkeys add"),
        ("verify", [], "cdkeys verify"),
    ]


def test_no_command_prints_usage_and_exits_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main([]) == 2
    assert "usage: cdkeys {add,verify}" in capsys.readouterr().out


def test_help_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--help"]) == 0
    assert "add or update licences" in capsys.readouterr().out


def test_unknown_command_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["upsert"]) == 2
    assert "unknown command 'upsert'" in capsys.readouterr().out


def test_real_subcommand_reports_its_own_prog_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        cli.main(["add", "--help"])

    assert "usage: cdkeys add" in capsys.readouterr().out
