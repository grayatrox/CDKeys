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
def root() -> Iterator[tk.Tk]:
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
