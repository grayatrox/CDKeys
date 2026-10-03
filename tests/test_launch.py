import sys
from collections.abc import Callable, Sequence
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

    with pytest.raises(launch.InstallError, match="will retry"):
        launch.install(root, venv, stamp, Recorder(fail_at=fail_at, venv=venv))

    assert not stamp.exists()
    assert launch.needs_install(venv, stamp, launch.expected_stamp(root))


def test_stale_stamp_is_removed_before_reinstalling(tmp_path: Path) -> None:
    root = _project(tmp_path)
    venv, stamp = root / ".venv", root / ".venv" / "stamp"
    _fake_venv(venv)
    stamp.write_text("old\n")

    with pytest.raises(launch.InstallError):
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


# --- double-click (GUI) start -------------------------------------------------


def test_cli_reports_install_failure_and_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path)
    monkeypatch.setattr(launch, "ROOT", root)
    monkeypatch.setattr(launch, "VENV", root / ".venv")
    monkeypatch.setattr(launch, "STAMP", root / ".venv" / "stamp")

    assert launch.main(["verify"], Recorder(fail_at=1, venv=root / ".venv")) == 1
    assert "will retry" in capsys.readouterr().err


def test_quiet_runner_captures_output_and_exit_code() -> None:
    log: list[str] = []
    run = launch.quiet_runner(log)

    rc = run([sys.executable, "-c", "print('hello'); import sys; sys.exit(3)"])

    assert rc == 3
    assert "hello" in "".join(log)


def test_failure_message_includes_the_tail_of_the_log() -> None:
    log = [f"line {i}\n" for i in range(50)]

    message = launch.failure_message(launch.InstallError("pip failed"), log)

    assert message.startswith("pip failed")
    assert "line 49" in message
    assert "line 10\n" not in message


def test_app_command_uses_console_less_python(tmp_path: Path) -> None:
    cmd = launch.app_command(tmp_path / ".venv")

    assert cmd[1:] == ["-m", "cdkeys.gui"]
    if sys.platform == "win32":
        assert cmd[0].endswith("pythonw.exe")


class GuiStart:
    def __init__(self, setup_error: BaseException | None = None) -> None:
        self.setup_error = setup_error
        self.setup_ran = False
        self.started: list[list[str]] = []
        self.errors: list[str] = []

    def run_setup(self, task: Callable[[], None]) -> BaseException | None:
        self.setup_ran = True
        if self.setup_error is None:
            task()
        return self.setup_error

    def start(self, cmd: list[str]) -> None:
        self.started.append(cmd)

    def show_error(self, message: str) -> None:
        self.errors.append(message)


def _gui_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: GuiStart, installed: bool
) -> int:
    root = _project(tmp_path)
    venv = root / ".venv"
    monkeypatch.setattr(launch, "ROOT", root)
    monkeypatch.setattr(launch, "VENV", venv)
    monkeypatch.setattr(launch, "STAMP", venv / "stamp")
    if installed:
        launch.install(root, venv, venv / "stamp", Recorder(venv=venv))
    return launch.gui_main(
        run_setup=fake.run_setup,
        start_app=fake.start,
        show_error=fake.show_error,
        runner_factory=lambda _log: Recorder(venv=venv),
    )


def test_gui_start_when_installed_skips_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = GuiStart()

    assert _gui_main(tmp_path, monkeypatch, fake, installed=True) == 0
    assert not fake.setup_ran
    assert fake.started == [launch.app_command(launch.VENV)]


def test_gui_first_start_installs_with_progress_then_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = GuiStart()

    assert _gui_main(tmp_path, monkeypatch, fake, installed=False) == 0
    assert fake.setup_ran
    assert fake.started == [launch.app_command(launch.VENV)]
    assert not launch.needs_install(
        launch.VENV, launch.STAMP, launch.expected_stamp(launch.ROOT)
    )


def test_gui_install_failure_is_shown_and_app_not_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = GuiStart(setup_error=launch.InstallError("pip failed"))

    assert _gui_main(tmp_path, monkeypatch, fake, installed=False) == 1
    assert fake.started == []
    assert len(fake.errors) == 1
    assert "pip failed" in fake.errors[0]


def test_progress_window_runs_task_and_returns_its_error() -> None:
    ran: list[bool] = []

    assert launch.run_with_progress(lambda: ran.append(True)) is None
    assert ran == [True]

    def boom() -> None:
        raise launch.InstallError("nope")

    error = launch.run_with_progress(boom)
    assert isinstance(error, launch.InstallError)
