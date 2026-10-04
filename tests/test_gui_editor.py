from collections.abc import Callable

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

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


# --- the real modal dialog ----------------------------------------------------


def _open_dialog(
    initial: LicenceFields | None, script: Callable[[LicenceDialog], None]
) -> LicenceFields | None:
    """Open the modal dialog via ask_licence; ``script`` runs once it shows."""

    def drive() -> None:
        dialog = QApplication.activeModalWidget()
        if not isinstance(dialog, LicenceDialog):
            QTimer.singleShot(10, drive)
            return
        script(dialog)

    QTimer.singleShot(0, drive)
    return ask_licence(None, "Edit licence", initial)


def _type(dialog: LicenceDialog, **values: str) -> None:
    for name, value in values.items():
        if name == "notes":
            dialog.notes.setPlainText(value)
        else:
            dialog.entries[name].setText(value)


@pytest.mark.usefixtures("qapp")
def test_dialog_prefills_and_returns_edited_fields() -> None:
    initial = LicenceFields(product_name="Office", product_key="K1", notes="a\nb")
    seen: dict[str, str] = {}

    def script(dialog: LicenceDialog) -> None:
        seen.update(dialog.values())
        _type(dialog, assigned_device="laptop", notes="new notes")
        dialog.accept()

    result = _open_dialog(initial, script)

    assert seen["product_key"] == "K1"
    assert seen["notes"] == "a\nb"
    assert result == LicenceFields(
        product_name="Office",
        product_key="K1",
        assigned_device="laptop",
        notes="new notes",
    )


@pytest.mark.usefixtures("qapp")
def test_dialog_explains_invalid_input_stays_open_then_cancel() -> None:
    observed: dict[str, object] = {}

    def script(dialog: LicenceDialog) -> None:
        _type(dialog, product_name="Office")  # no key, serial, login or identity
        dialog.accept()
        observed["visible"] = dialog.isVisible()
        observed["problem"] = dialog.problem.text()
        observed["problem_shown"] = not dialog.problem.isHidden()
        dialog.reject()

    result = _open_dialog(None, script)

    assert result is None
    assert observed["visible"] is True
    assert observed["problem_shown"] is True
    assert "Enter a product key" in str(observed["problem"])
