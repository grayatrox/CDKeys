import logging
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QPushButton

from cdkeys.gui.errors import (
    configure_logging,
    error_message,
    log_path,
    make_excepthook,
)


@pytest.fixture
def log_file(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "logs" / "cdkeys.log"
    handler = configure_logging(path)
    yield path
    logging.getLogger("cdkeys").removeHandler(handler)
    handler.close()


def test_log_lives_in_the_per_user_config_dir(tmp_path: Path) -> None:
    path = log_path({"APPDATA": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path)})

    assert path == tmp_path / "cdkeys" / "cdkeys.log"


def test_error_message_names_the_error_and_the_log() -> None:
    message = error_message(ValueError("bad thing"), Path("C:/x/cdkeys.log"))

    assert "ValueError: bad thing" in message
    assert "cdkeys.log" in message


def test_hook_logs_traceback_and_shows_message(log_file: Path) -> None:
    shown: list[str] = []
    hook = make_excepthook(log_file, shown.append)

    try:
        raise OSError("disk on fire")
    except OSError as e:
        hook(type(e), e, e.__traceback__)

    assert len(shown) == 1
    assert "OSError: disk on fire" in shown[0]
    log = log_file.read_text(encoding="utf-8")
    assert "Traceback" in log
    assert "disk on fire" in log


@pytest.mark.usefixtures("qapp")
def test_error_in_a_button_click_reaches_the_hook(
    log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown: list[str] = []
    monkeypatch.setattr(sys, "excepthook", make_excepthook(log_file, shown.append))

    def broken() -> None:
        raise RuntimeError("button exploded")

    button = QPushButton()
    button.clicked.connect(broken)
    button.click()

    assert len(shown) == 1
    assert "RuntimeError: button exploded" in shown[0]
    assert "button exploded" in log_file.read_text(encoding="utf-8")


def test_keyboard_interrupt_is_not_shown(
    log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown: list[str] = []
    passed_on: list[type[BaseException]] = []
    monkeypatch.setattr(sys, "__excepthook__", lambda t, _e, _tb: passed_on.append(t))
    hook = make_excepthook(log_file, shown.append)

    hook(KeyboardInterrupt, KeyboardInterrupt(), None)

    assert shown == []
    assert passed_on == [KeyboardInterrupt]
