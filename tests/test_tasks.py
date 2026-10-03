import pytest

import tasks


def test_exposes_the_standard_verbs() -> None:
    assert {"setup", "fmt", "lint", "typecheck", "test", "run"} <= set(tasks.TASKS)


@pytest.mark.parametrize("argv", [[], ["nope"]])
def test_unknown_or_missing_verb_prints_usage_and_exits_2(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert tasks.main(argv) == 2
    assert "python tasks.py setup" in capsys.readouterr().out


@pytest.mark.parametrize("args", [[], ["nope"]])
def test_run_requires_a_known_target(
    args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert tasks.run(args) == 2
    assert "upsert" in capsys.readouterr().out
