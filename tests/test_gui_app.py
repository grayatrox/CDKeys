"""Smoke tests driving the real Tk main window against an in-memory DB."""

import tkinter as tk
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlcipher3

from cdkeys.db import DBConn, ensure_schema
from cdkeys.gui.app import KeyManagerApp
from cdkeys.gui.format import MASK
from cdkeys.store import LicenceFields, add_licence


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


@pytest.fixture
def root(con: DBConn) -> Iterator[tk.Tk]:
    # Depends on con so the window is torn down while the DB is still open.
    r = tk.Tk()
    r.withdraw()
    # Copy tests use the real system clipboard; put back what was there.
    try:
        saved: str | None = r.clipboard_get()
    except tk.TclError:  # clipboard empty or not text
        saved = None
    yield r
    r.clipboard_clear()
    if saved is not None:
        r.clipboard_append(saved)
        r.update()
    r.destroy()


def _app(root: tk.Tk, con: DBConn) -> KeyManagerApp:
    app = KeyManagerApp(root, con, Path("test.sqlite3"))
    root.update()
    return app


def _products(app: KeyManagerApp) -> list[str]:
    return [app.tree.set(iid, "product") for iid in app.tree.get_children()]


def test_lists_licences_sorted_with_masked_keys(root: tk.Tk, con: DBConn) -> None:
    app = _app(root, con)

    assert _products(app) == ["Game", "Office"]
    office = app.tree.get_children()[1]
    assert app.tree.set(office, "key") == MASK * 13 + "CCCC"


def test_search_filters_rows(root: tk.Tk, con: DBConn) -> None:
    app = _app(root, con)

    app.search_var.set("LAPTOP")
    root.update()
    assert _products(app) == ["Office"]

    app.search_var.set("")
    root.update()
    assert _products(app) == ["Game", "Office"]


def test_selecting_a_row_fills_details(root: tk.Tk, con: DBConn) -> None:
    app = _app(root, con)

    app.tree.selection_set(app.tree.get_children()[1])
    root.update()

    assert app.detail_vars["product_name"].get() == "Office"
    assert app.detail_vars["product_key"].get() == "AAAAA-BBBBB-CCCCC"
    assert app.detail_entries["product_key"].cget("show") == MASK


def test_show_toggle_reveals_secrets(root: tk.Tk, con: DBConn) -> None:
    app = _app(root, con)

    app.show_secrets.set(True)
    app._apply_secret_visibility()

    assert app.detail_entries["product_key"].cget("show") == ""
    assert app.detail_entries["serial_number"].cget("show") == ""


def test_copy_puts_value_on_clipboard_without_echoing_it(
    root: tk.Tk, con: DBConn
) -> None:
    app = _app(root, con)
    app.tree.selection_set(app.tree.get_children()[1])
    root.update()

    app.copy_field("product_key", "Product key")

    assert root.clipboard_get() == "AAAAA-BBBBB-CCCCC"
    assert "AAAAA" not in app.status_var.get()
    assert "product key" in app.status_var.get()


def test_copy_reports_empty_field_and_no_selection(root: tk.Tk, con: DBConn) -> None:
    app = _app(root, con)

    app.copy_field("product_key", "Product key")
    assert app.status_var.get() == "Select a licence first."

    app.tree.selection_set(app.tree.get_children()[0])  # Game: no key
    root.update()
    app.copy_field("product_key", "Product key")
    assert app.status_var.get() == "Game has no product key."


class Scripted:
    """Stands in for the modal dialogs, answering from queues."""

    def __init__(
        self,
        answers: list[LicenceFields | None],
        confirm: bool = True,
    ) -> None:
        self.answers = answers
        self.confirm_answer = confirm
        self.asked: list[tuple[str, LicenceFields | None]] = []
        self.errors: list[str] = []
        self.confirms: list[str] = []

    def ask(
        self, _parent: tk.Misc, title: str, initial: LicenceFields | None
    ) -> LicenceFields | None:
        self.asked.append((title, initial))
        return self.answers.pop(0)

    def confirm(self, _parent: tk.Misc, message: str) -> bool:
        self.confirms.append(message)
        return self.confirm_answer

    def show_error(self, _parent: tk.Misc, message: str) -> None:
        self.errors.append(message)


def _scripted_app(root: tk.Tk, con: DBConn, script: Scripted) -> KeyManagerApp:
    app = KeyManagerApp(
        root,
        con,
        Path("test.sqlite3"),
        ask=script.ask,
        confirm=script.confirm,
        show_error=script.show_error,
    )
    root.update()
    return app


def _select(app: KeyManagerApp, product: str) -> None:
    for iid in app.tree.get_children():
        if app.tree.set(iid, "product") == product:
            app.tree.selection_set(iid)
            app.update()
            return
    raise AssertionError(f"{product} not listed")


def test_edit_and_delete_disabled_until_a_row_is_selected(
    root: tk.Tk, con: DBConn
) -> None:
    app = _scripted_app(root, con, Scripted([]))

    assert str(app.edit_button.cget("state")) == "disabled"
    _select(app, "Office")
    assert str(app.edit_button.cget("state")) == "normal"
    assert str(app.delete_button.cget("state")) == "normal"


def test_add_saves_selects_and_clears_search(root: tk.Tk, con: DBConn) -> None:
    script = Scripted([LicenceFields(product_name="Windows", product_key="W1")])
    app = _scripted_app(root, con, script)
    app.search_var.set("office")
    root.update()

    app.add()

    assert app.search_var.get() == ""
    assert _products(app) == ["Game", "Office", "Windows"]
    assert app.detail_vars["product_name"].get() == "Windows"
    assert app.status_var.get() == "Added licence for Windows."
    assert con.execute("SELECT COUNT(*) FROM license").fetchone() == (3,)


def test_add_duplicate_shows_error_and_reopens_with_input(
    root: tk.Tk, con: DBConn
) -> None:
    duplicate = LicenceFields(product_name="office", product_key="AAAAA-BBBBB-CCCCC")
    script = Scripted([duplicate, None])
    app = _scripted_app(root, con, script)

    app.add()

    assert len(script.errors) == 1
    assert "already stored" in script.errors[0]
    assert script.asked[1] == ("Add licence", duplicate)  # input kept
    assert _products(app) == ["Game", "Office"]


def test_add_cancelled_changes_nothing(root: tk.Tk, con: DBConn) -> None:
    app = _scripted_app(root, con, Scripted([None]))

    app.add()

    assert _products(app) == ["Game", "Office"]


def test_edit_prefills_saves_and_keeps_selection(root: tk.Tk, con: DBConn) -> None:
    edited = LicenceFields(
        product_name="Office",
        product_key="AAAAA-BBBBB-DDDDD",
        assigned_device="desktop",
    )
    script = Scripted([edited])
    app = _scripted_app(root, con, script)
    _select(app, "Office")

    app.edit()

    title, initial = script.asked[0]
    assert title == "Edit licence"
    assert initial is not None and initial.product_key == "AAAAA-BBBBB-CCCCC"
    assert app.detail_vars["product_key"].get() == "AAAAA-BBBBB-DDDDD"
    assert app.detail_vars["assigned_device"].get() == "desktop"
    assert app.status_var.get() == "Saved licence for Office."


def test_edit_collision_shows_error_and_rolls_back(root: tk.Tk, con: DBConn) -> None:
    clash = LicenceFields(
        product_name="Game", serial_number="SN-1", associated_login="me@x.com"
    )
    script = Scripted([clash, None])
    app = _scripted_app(root, con, script)
    _select(app, "Office")

    app.edit()

    assert len(script.errors) == 1
    assert "another stored licence" in script.errors[0]
    assert _products(app) == ["Game", "Office"]
    assert app.detail_vars["product_key"].get() == "AAAAA-BBBBB-CCCCC"


def test_delete_confirms_without_showing_key_then_removes(
    root: tk.Tk, con: DBConn
) -> None:
    script = Scripted([], confirm=True)
    app = _scripted_app(root, con, script)
    _select(app, "Office")

    app.delete()

    assert script.confirms == ["Delete the licence for Office?"]
    assert _products(app) == ["Game"]
    assert app.selected is None
    assert app.status_var.get() == "Deleted licence for Office."


def test_delete_declined_keeps_licence(root: tk.Tk, con: DBConn) -> None:
    app = _scripted_app(root, con, Scripted([], confirm=False))
    _select(app, "Office")

    app.delete()

    assert _products(app) == ["Game", "Office"]
