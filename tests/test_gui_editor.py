import tkinter as tk
from collections.abc import Callable, Iterator
from tkinter import messagebox

import pytest

from cdkeys.gui.editor import (
    LicenceDialog,
    ask_licence,
    fields_from_form,
    form_values,
    validate_form,
)
from cdkeys.store import LicenceFields


def _values(**kw: str) -> dict[str, str]:
    values = form_values(None)
    values.update(kw)
    return values


def test_form_values_round_trip_through_fields() -> None:
    fields = LicenceFields(
        product_name="Office",
        product_key="K1",
        serial_number=None,
        associated_login="me@x",
        assigned_device="pc",
        notes="line 1\nline 2",
        identity="INV-1",
    )

    assert fields_from_form(form_values(fields)) == fields


def test_blank_form_fields_become_none_and_text_is_trimmed() -> None:
    fields = fields_from_form(
        _values(product_name="  Office ", product_key=" K1 ", notes="   ")
    )

    assert fields == LicenceFields(product_name="Office", product_key="K1")


@pytest.mark.parametrize(
    ("values", "problem"),
    [
        ({"product_name": "", "product_key": "K"}, "Product name is required"),
        ({"product_name": "Office"}, "Enter a product key"),
        ({"product_name": "Office", "assigned_device": "pc"}, "Enter a product key"),
    ],
)
def test_validate_form_explains_problems(values: dict[str, str], problem: str) -> None:
    message = validate_form(_values(**values))

    assert message is not None
    assert problem in message


@pytest.mark.parametrize(
    "values",
    [
        {"product_name": "Office", "product_key": "K"},
        {"product_name": "Office", "serial_number": "S"},
        {"product_name": "Office", "associated_login": "me"},
        {"product_name": "Office", "identity": "INV-1"},
    ],
)
def test_validate_form_accepts_any_identifying_field(values: dict[str, str]) -> None:
    assert validate_form(_values(**values)) is None


# --- the real modal dialog, driven with Tk timers ---------------------------


@pytest.fixture
def root() -> Iterator[tk.Tk]:
    r = tk.Tk()
    r.withdraw()
    yield r
    r.destroy()


def _open_dialog(
    root: tk.Tk, initial: LicenceFields | None, script: Callable[[LicenceDialog], None]
) -> LicenceFields | None:
    """Open the dialog; ``script`` runs once it is showing."""

    def drive() -> None:
        dialogs = [w for w in root.winfo_children() if isinstance(w, LicenceDialog)]
        if not dialogs:
            root.after(20, drive)
            return
        script(dialogs[0])

    root.after(20, drive)
    return ask_licence(root, "Edit licence", initial)


def _type(dialog: LicenceDialog, **values: str) -> None:
    for name, value in values.items():
        if name == "notes":
            dialog.notes.delete("1.0", "end")
            dialog.notes.insert("1.0", value)
        else:
            dialog.entries[name].delete(0, "end")
            dialog.entries[name].insert(0, value)


def test_dialog_prefills_and_returns_edited_fields(root: tk.Tk) -> None:
    initial = LicenceFields(product_name="Office", product_key="K1", notes="a\nb")
    seen: dict[str, str] = {}

    def script(dialog: LicenceDialog) -> None:
        seen.update(dialog.values())
        _type(dialog, assigned_device="laptop", notes="new notes")
        dialog.ok()

    result = _open_dialog(root, initial, script)

    assert seen["product_key"] == "K1"
    assert seen["notes"] == "a\nb"
    assert result == LicenceFields(
        product_name="Office",
        product_key="K1",
        assigned_device="laptop",
        notes="new notes",
    )


def test_dialog_refuses_invalid_input_then_cancel_returns_none(
    root: tk.Tk, monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(
        messagebox, "showwarning", lambda _t, msg, **_k: warnings.append(msg)
    )
    still_open: list[bool] = []

    def script(dialog: LicenceDialog) -> None:
        _type(dialog, product_name="Office")  # no key, serial, login or identity
        dialog.ok()
        still_open.append(bool(dialog.winfo_exists()))
        dialog.cancel()

    result = _open_dialog(root, None, script)

    assert result is None
    assert still_open == [True]
    assert len(warnings) == 1
    assert "Enter a product key" in warnings[0]
