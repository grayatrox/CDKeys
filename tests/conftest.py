import os
from collections.abc import Iterator

import pytest

# Render Qt widgets in memory: no windows appear and CI needs no display.
# Set before any QApplication exists; the clipboard is Qt's own, not the OS's.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp() -> Iterator[object]:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
