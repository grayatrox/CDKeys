"""The add/edit licence form."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from cdkeys.gui.format import DETAIL_FIELDS
from cdkeys.licenses import norm
from cdkeys.store import InvalidLicenceError, LicenceFields, licence_id_for

MULTILINE = "notes"
HINT = (
    "Product is required, plus a product key, serial number or login. "
    "For licences without any of those, enter an identity such as an "
    "invoice number."
)


def form_values(fields: LicenceFields | None) -> dict[str, str]:
    """Initial text for each form field."""
    return {
        name: (getattr(fields, name) or "") if fields else ""
        for name, _ in DETAIL_FIELDS
    }


def fields_from_form(values: dict[str, str]) -> LicenceFields:
    return LicenceFields(
        product_name=values["product_name"].strip(),
        product_key=norm(values["product_key"]),
        serial_number=norm(values["serial_number"]),
        associated_login=norm(values["associated_login"]),
        assigned_device=norm(values["assigned_device"]),
        notes=norm(values["notes"]),
        identity=norm(values["identity"]),
    )


def validate_form(values: dict[str, str]) -> str | None:
    """A message explaining why the form cannot be saved, or None."""
    try:
        licence_id_for(fields_from_form(values))
    except InvalidLicenceError as e:
        return str(e)
    return None


class LicenceDialog(QDialog):
    """Modal add/edit form. ``result_fields`` is set when accepted."""

    def __init__(
        self, parent: QWidget | None, title: str, initial: LicenceFields | None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self.result_fields: LicenceFields | None = None
        self.entries: dict[str, QLineEdit] = {}

        form = QFormLayout()
        start = form_values(initial)
        for name, label in DETAIL_FIELDS:
            if name == MULTILINE:
                self.notes = QPlainTextEdit(start[name])
                self.notes.setFixedHeight(80)
                form.addRow(label, self.notes)
            else:
                entry = QLineEdit(start[name])
                entry.setClearButtonEnabled(True)
                form.addRow(label, entry)
                self.entries[name] = entry

        hint = QLabel(HINT)
        hint.setWordWrap(True)
        hint.setEnabled(False)  # rendered in the theme's muted colour
        self.problem = QLabel()
        self.problem.setWordWrap(True)
        self.problem.setObjectName("problem")
        self.problem.setStyleSheet("color: #c42b1c;")
        self.problem.hide()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(self.problem)
        layout.addWidget(buttons)
        self.entries["product_name"].setFocus()

    def values(self) -> dict[str, str]:
        values = {name: entry.text() for name, entry in self.entries.items()}
        values[MULTILINE] = self.notes.toPlainText()
        return values

    def accept(self) -> None:
        problem = validate_form(self.values())
        if problem:
            self.problem.setText(problem)
            self.problem.show()
            return
        self.result_fields = fields_from_form(self.values())
        super().accept()


def ask_licence(
    parent: QWidget | None, title: str, initial: LicenceFields | None
) -> LicenceFields | None:
    dialog = LicenceDialog(parent, title, initial)
    dialog.exec()
    return dialog.result_fields
