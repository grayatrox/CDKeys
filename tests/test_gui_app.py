"""Behaviour tests driving the real Qt main window against an in-memory DB."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import sqlcipher3
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLineEdit, QWidget

from cdkeys.db import DBConn, ensure_schema
from cdkeys.gui.app import KeyManagerWindow
from cdkeys.gui.format import MASK
from cdkeys.store import LicenceFields, add_licence

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture
def con() -> Iterator[DBConn]:
    c: DBConn = sqlcipher3.connect(":memory:")
    ensure_schema(c)
    add_licence(
        c,
        LicenceFields(
            product_name="Office",
            product_key="AAAAA-BBBBB-CCCCC",
            assigned_device="laptop",
        ),
    )
    add_licence(
        c,
        LicenceFields(
            product_name="Game",
            serial_number="SN-1",
            associated_login="me@x.com",
            notes="boxed",
        ),
    )
    yield c
    c.close()


class Scripted:
    """Stands in for the modal dialogs, answering from queues."""

    def __init__(
        self, answers: list[LicenceFields | None] | None = None, confirm: bool = True
    ) -> None:
        self.answers = answers or []
        self.confirm_answer = confirm
        self.asked: list[tuple[str, LicenceFields | None]] = []
        self.errors: list[str] = []
        self.confirms: list[str] = []

    def ask(
        self, _parent: QWidget | None, title: str, initial: LicenceFields | None
    ) -> LicenceFields | None:
        self.asked.append((title, initial))
        return self.answers.pop(0)

    def confirm(self, _parent: QWidget, message: str) -> bool:
        self.confirms.append(message)
        return self.confirm_answer

    def show_error(self, _parent: QWidget, message: str) -> None:
        self.errors.append(message)


WindowFactory = Callable[[Scripted], KeyManagerWindow]


@pytest.fixture
def make_window(con: DBConn) -> Iterator[WindowFactory]:
    windows: list[KeyManagerWindow] = []

    def make(script: Scripted) -> KeyManagerWindow:
        window = KeyManagerWindow(
            con,
            Path("test.sqlite3"),
            ask=script.ask,
            confirm=script.confirm,
            show_error=script.show_error,
        )
        windows.append(window)
        return window

    yield make
    # Close windows while the DB is still open (con is torn down after this).
    for window in windows:
        window.close()
        window.deleteLater()


def _products(window: KeyManagerWindow) -> list[str]:
    proxy = window.proxy
    return [str(proxy.index(r, 0).data()) for r in range(proxy.rowCount())]


def _cell(window: KeyManagerWindow, product: str, column: int) -> str:
    proxy = window.proxy
    for r in range(proxy.rowCount()):
        if proxy.index(r, 0).data() == product:
            return str(proxy.index(r, column).data())
    raise AssertionError(f"{product} not listed")


def _select(window: KeyManagerWindow, product: str) -> None:
    proxy = window.proxy
    for r in range(proxy.rowCount()):
        if proxy.index(r, 0).data() == product:
            window.table.selectRow(r)
            return
    raise AssertionError(f"{product} not listed")


def _field(window: KeyManagerWindow, name: str) -> str:
    field = window.fields[name]
    return field.text() if isinstance(field, QLineEdit) else field.toPlainText()


def _rows(con: DBConn) -> object:
    return con.execute("SELECT COUNT(*) FROM license").fetchone()


# --- listing, search, details, copy -----------------------------------------


def test_lists_licences_sorted_with_masked_keys(make_window: WindowFactory) -> None:
    window = make_window(Scripted())

    assert _products(window) == ["Game", "Office"]
    assert _cell(window, "Office", 1) == MASK * 6 + "CCCC"


def test_sorting_by_a_column_reorders_rows(make_window: WindowFactory) -> None:
    window = make_window(Scripted())

    window.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
    assert _products(window) == ["Office", "Game"]

    window.table.sortByColumn(2, Qt.SortOrder.AscendingOrder)  # device
    assert _products(window) == ["Game", "Office"]  # "" sorts before "laptop"


def test_search_filters_rows(make_window: WindowFactory) -> None:
    window = make_window(Scripted())

    window.search.setText("LAPTOP")
    assert _products(window) == ["Office"]

    window.search.clear()
    assert _products(window) == ["Game", "Office"]


def test_selecting_a_row_fills_details_with_secrets_hidden(
    make_window: WindowFactory,
) -> None:
    window = make_window(Scripted())

    _select(window, "Office")

    assert _field(window, "product_name") == "Office"
    assert _field(window, "product_key") == "AAAAA-BBBBB-CCCCC"
    key_field = window.fields["product_key"]
    assert isinstance(key_field, QLineEdit)
    assert key_field.echoMode() == QLineEdit.EchoMode.Password


def test_show_toggle_reveals_secrets(make_window: WindowFactory) -> None:
    window = make_window(Scripted())

    window.show_secrets.setChecked(True)

    for name in ("product_key", "serial_number"):
        field = window.fields[name]
        assert isinstance(field, QLineEdit)
        assert field.echoMode() == QLineEdit.EchoMode.Normal


def test_copy_puts_value_on_clipboard_without_echoing_it(
    make_window: WindowFactory,
) -> None:
    window = make_window(Scripted())
    _select(window, "Office")

    window.copy_field("product_key", "Product key")

    assert QGuiApplication.clipboard().text() == "AAAAA-BBBBB-CCCCC"
    assert "AAAAA" not in window.status()
    assert "product key" in window.status()


def test_copy_reports_empty_field_and_no_selection(make_window: WindowFactory) -> None:
    window = make_window(Scripted())

    window.copy_field("product_key", "Product key")
    assert window.status() == "Select a licence first."

    _select(window, "Game")  # no key
    window.copy_field("product_key", "Product key")
    assert window.status() == "Game has no product key."


# --- add / edit / delete ------------------------------------------------------


def test_edit_and_delete_disabled_until_a_row_is_selected(
    make_window: WindowFactory,
) -> None:
    window = make_window(Scripted())

    assert not window.edit_action.isEnabled()
    _select(window, "Office")
    assert window.edit_action.isEnabled()
    assert window.delete_action.isEnabled()


def test_add_saves_selects_and_clears_search(
    make_window: WindowFactory, con: DBConn
) -> None:
    window = make_window(
        Scripted([LicenceFields(product_name="Windows", product_key="W1")])
    )
    window.search.setText("office")

    window.add()

    assert window.search.text() == ""
    assert _products(window) == ["Game", "Office", "Windows"]
    assert _field(window, "product_name") == "Windows"
    assert window.status() == "Added licence for Windows."
    assert _rows(con) == (3,)


def test_add_duplicate_shows_error_and_reopens_with_input(
    make_window: WindowFactory,
) -> None:
    duplicate = LicenceFields(product_name="office", product_key="AAAAA-BBBBB-CCCCC")
    script = Scripted([duplicate, None])
    window = make_window(script)

    window.add()

    assert len(script.errors) == 1
    assert "already stored" in script.errors[0]
    assert script.asked[1] == ("Add licence", duplicate)  # input kept
    assert _products(window) == ["Game", "Office"]


def test_add_cancelled_changes_nothing(make_window: WindowFactory) -> None:
    window = make_window(Scripted([None]))

    window.add()

    assert _products(window) == ["Game", "Office"]


def test_edit_prefills_saves_and_keeps_selection(make_window: WindowFactory) -> None:
    edited = LicenceFields(
        product_name="Office",
        product_key="AAAAA-BBBBB-DDDDD",
        assigned_device="desktop",
    )
    script = Scripted([edited])
    window = make_window(script)
    _select(window, "Office")

    window.edit()

    title, initial = script.asked[0]
    assert title == "Edit licence"
    assert initial is not None and initial.product_key == "AAAAA-BBBBB-CCCCC"
    assert _field(window, "product_key") == "AAAAA-BBBBB-DDDDD"
    assert _field(window, "assigned_device") == "desktop"
    assert window.status() == "Saved licence for Office."


def test_edit_collision_shows_error_and_rolls_back(make_window: WindowFactory) -> None:
    clash = LicenceFields(
        product_name="Game", serial_number="SN-1", associated_login="me@x.com"
    )
    script = Scripted([clash, None])
    window = make_window(script)
    _select(window, "Office")

    window.edit()

    assert len(script.errors) == 1
    assert "another stored licence" in script.errors[0]
    assert _products(window) == ["Game", "Office"]
    assert _field(window, "product_key") == "AAAAA-BBBBB-CCCCC"


def test_delete_confirms_without_showing_key_then_removes(
    make_window: WindowFactory,
) -> None:
    script = Scripted(confirm=True)
    window = make_window(script)
    _select(window, "Office")

    window.delete()

    assert script.confirms == ["Delete the licence for Office?"]
    assert _products(window) == ["Game"]
    assert window.selected is None
    assert window.status() == "Deleted licence for Office."


def test_delete_declined_keeps_licence(make_window: WindowFactory) -> None:
    window = make_window(Scripted(confirm=False))
    _select(window, "Office")

    window.delete()

    assert _products(window) == ["Game", "Office"]


def test_double_click_opens_edit(make_window: WindowFactory) -> None:
    script = Scripted([None])
    window = make_window(script)
    _select(window, "Office")

    window.table.doubleClicked.emit(window.proxy.index(1, 0))

    assert script.asked and script.asked[0][0] == "Edit licence"
