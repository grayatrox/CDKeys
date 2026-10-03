"""The add/edit licence form."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

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


class LicenceDialog(simpledialog.Dialog):
    """Modal add/edit form. ``result_fields`` is None if cancelled."""

    def __init__(
        self, parent: tk.Misc, title: str, initial: LicenceFields | None
    ) -> None:
        self.initial = form_values(initial)
        self.result_fields: LicenceFields | None = None
        self.entries: dict[str, ttk.Entry] = {}
        super().__init__(parent, title)

    def body(self, master: tk.Frame) -> tk.Widget:
        for row, (name, label) in enumerate(DETAIL_FIELDS):
            ttk.Label(master, text=label + ":").grid(
                row=row, column=0, sticky="nw", pady=2
            )
            if name == MULTILINE:
                self.notes = tk.Text(master, width=48, height=4, wrap="word")
                self.notes.insert("1.0", self.initial[name])
                self.notes.grid(row=row, column=1, pady=2, sticky="ew")
            else:
                entry = ttk.Entry(master, width=48)
                entry.insert(0, self.initial[name])
                entry.grid(row=row, column=1, pady=2, sticky="ew")
                self.entries[name] = entry
        ttk.Label(master, text=HINT, wraplength=420, foreground="gray").grid(
            row=len(DETAIL_FIELDS), column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        return self.entries["product_name"]

    def values(self) -> dict[str, str]:
        values = {name: entry.get() for name, entry in self.entries.items()}
        values[MULTILINE] = self.notes.get("1.0", "end-1c")
        return values

    def validate(self) -> bool:
        problem = validate_form(self.values())
        if problem:
            messagebox.showwarning("Licence", problem, parent=self)
            return False
        return True

    def apply(self) -> None:
        self.result_fields = fields_from_form(self.values())


def ask_licence(
    parent: tk.Misc, title: str, initial: LicenceFields | None
) -> LicenceFields | None:
    return LicenceDialog(parent, title, initial).result_fields
