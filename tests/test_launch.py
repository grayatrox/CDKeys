from collections.abc import Sequence
from pathlib import Path

import pytest

import launch


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "requirements-runtime.lock").write_text("sqlcipher3==0.6.2\n")
    (root / "pyproject.toml").write_text("[project]\nname = 'cdkeys'\n")
    return root


def _fake_venv(venv: Path) -> None:
    python = launch.venv_python(venv)
    python.parent.mkdir(parents=True)
    python.touch()


class Recorder:
    def __init__(self, fail_at: int | None = None, venv: Path | None = None) -> None:
        self.cmds: list[list[str]] = []
        self.fail_at = fail_at
        self.venv = venv

    def __call__(self, cmd: Sequence[str]) -> int:
        self.cmds.append(list(cmd))
        if self.venv is not None and cmd[1:3] == ["-m", "venv"]:
            _fake_venv(self.venv)
        return 1 if len(self.cmds) - 1 == self.fail_at else 0


def test_fresh_checkout_needs_install(tmp_path: Path) -> None:
    root = _project(tmp_path)
    venv = root / ".venv"

    assert launch.needs_install(venv, venv / "stamp", launch.expected_stamp(root))


def test_install_creates_venv_installs_and_stamps(tmp_path: Path) -> None:
    root = _project(tmp_path)
    venv, stamp = root / ".venv", root / ".venv" / "stamp"
    run = Recorder(venv=venv)

    launch.install(root, venv, stamp, run)

    assert [cmd[1:3] for cmd in run.cmds] == [
        ["-m", "venv"],
        ["-m", "pip"],
        ["-m", "pip"],
    ]
    assert str(root / "requirements-runtime.lock") in run.cmds[1]
    assert run.cmds[2][-2:] == ["-e", str(root)]
    assert not launch.needs_install(venv, stamp, launch.expected_stamp(root))


def test_existing_venv_is_reused_not_recreated(tmp_path: Path) -> None:
    root = _project(tmp_path)
    venv, stamp = root / ".venv", root / ".venv" / "stamp"
    _fake_venv(venv)
    run = Recorder()

    launch.install(root, venv, stamp, run)

    assert [cmd[1:3] for cmd in run.cmds] == [["-m", "pip"], ["-m", "pip"]]


def test_changed_lock_triggers_reinstall(tmp_path: Path) -> None:
    root = _project(tmp_path)
    venv, stamp = root / ".venv", root / ".venv" / "stamp"
    launch.install(root, venv, stamp, Recorder(venv=venv))

    (root / "requirements-runtime.lock").write_text("sqlcipher3==0.6.3\n")

    assert launch.needs_install(venv, stamp, launch.expected_stamp(root))


@pytest.mark.parametrize("fail_at", [0, 1, 2])
def test_failed_step_leaves_no_stamp_so_next_start_retries(
    tmp_path: Path, fail_at: int
) -> None:
    root = _project(tmp_path)
    venv, stamp = root / ".venv", root / ".venv" / "stamp"

    with pytest.raises(SystemExit, match="will retry"):
        launch.install(root, venv, stamp, Recorder(fail_at=fail_at, venv=venv))

    assert not stamp.exists()
    assert launch.needs_install(venv, stamp, launch.expected_stamp(root))


def test_stale_stamp_is_removed_before_reinstalling(tmp_path: Path) -> None:
    root = _project(tmp_path)
    venv, stamp = root / ".venv", root / ".venv" / "stamp"
    _fake_venv(venv)
    stamp.write_text("old\n")

    with pytest.raises(SystemExit):
        launch.install(root, venv, stamp, Recorder(fail_at=0))

    assert not stamp.exists()


def test_main_runs_cdkeys_in_the_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    venv = root / ".venv"
    monkeypatch.setattr(launch, "ROOT", root)
    monkeypatch.setattr(launch, "VENV", venv)
    monkeypatch.setattr(launch, "STAMP", venv / "stamp")
    run = Recorder(venv=venv)

    assert launch.main(["verify", "--db", "x"], run) == 0
    assert run.cmds[-1] == [
        str(launch.venv_python(venv)),
        "-m",
        "cdkeys",
        "verify",
        "--db",
        "x",
    ]

    run.cmds.clear()
    assert launch.main(["verify"], run) == 0
    assert run.cmds == [[str(launch.venv_python(venv)), "-m", "cdkeys", "verify"]]
