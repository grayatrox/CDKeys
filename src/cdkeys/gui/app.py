"""The key manager main window: search, licence table, details, add/edit/delete."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from functools import partial
from pathlib import Path
from tkinter import messagebox, ttk

from cdkeys.db import DBConn
from cdkeys.gui.editor import ask_licence
from cdkeys.gui.format import (
    COLUMNS,
    DETAIL_FIELDS,
    MASK,
    SECRET_FIELDS,
    copied_message,
    row_values,
)
from cdkeys.store import (
    Licence,
    LicenceError,
    LicenceFields,
    add_licence,
    delete_licence,
    get_licence,
    list_licences,
    update_licence,
)

PAD = 6
TITLE = "CD Key Manager"

# Injected so tests can drive the window without blocking modal dialogs.
AskLicence = Callable[[tk.Misc, str, LicenceFields | None], LicenceFields | None]
Confirm = Callable[[tk.Misc, str], bool]
ShowError = Callable[[tk.Misc, str], None]


def _confirm(parent: tk.Misc, message: str) -> bool:
    return messagebox.askyesno(TITLE, message, parent=parent)


def _show_error(parent: tk.Misc, message: str) -> None:
    messagebox.showerror(TITLE, message, parent=parent)


class KeyManagerApp(ttk.Frame):
    """Main window contents. The caller owns ``con`` and closes it."""

    def __init__(
        self,
        master: tk.Tk,
        con: DBConn,
        db_path: Path,
        ask: AskLicence = ask_licence,
        confirm: Confirm = _confirm,
        show_error: ShowError = _show_error,
    ) -> None:
        super().__init__(master, padding=PAD)
        self.con = con
        self.ask = ask
        self.confirm = confirm
        self.show_error = show_error
        self.selected: Licence | None = None
        self.search_var = tk.StringVar(master=self)
        self.status_var = tk.StringVar(master=self, value=f"Database: {db_path}")
        self.show_secrets = tk.BooleanVar(master=self, value=False)
        self.detail_vars = {
            name: tk.StringVar(master=self) for name, _ in DETAIL_FIELDS
        }
        self.detail_entries: dict[str, ttk.Entry] = {}

        master.title(TITLE)
        master.minsize(760, 480)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)

        self._build_toolbar()
        self._build_table()
        self._build_details()
        ttk.Label(self, textvariable=self.status_var, anchor="w").grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(PAD, 0)
        )
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=1)

        self.search_var.trace_add("write", lambda *_: self.refresh())
        self.refresh()

    # --- layout -------------------------------------------------------------

    def _build_toolbar(self) -> None:
        self.toolbar = ttk.Frame(self)
        self.toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, PAD))
        ttk.Button(self.toolbar, text="Add…", command=self.add).pack(side="left")
        self.edit_button = ttk.Button(
            self.toolbar, text="Edit…", command=self.edit, state="disabled"
        )
        self.edit_button.pack(side="left", padx=(PAD, 0))
        self.delete_button = ttk.Button(
            self.toolbar, text="Delete", command=self.delete, state="disabled"
        )
        self.delete_button.pack(side="left", padx=(PAD, PAD * 3))
        ttk.Label(self.toolbar, text="Search:").pack(side="left")
        self.search_entry = ttk.Entry(self.toolbar, textvariable=self.search_var)
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(PAD, 0))

    def _build_table(self) -> None:
        frame = ttk.Frame(self)
        frame.grid(row=1, column=0, sticky="nsew", padx=(0, PAD))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            frame,
            columns=[cid for cid, _ in COLUMNS],
            show="headings",
            selectmode="browse",
        )
        for cid, heading in COLUMNS:
            self.tree.heading(cid, text=heading)
            self.tree.column(cid, width=120, stretch=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())
        self.tree.bind("<Double-1>", lambda _e: self.edit())
        self.tree.bind("<Delete>", lambda _e: self.delete())

    def _build_details(self) -> None:
        box = ttk.LabelFrame(self, text="Details", padding=PAD)
        box.grid(row=1, column=1, sticky="nsew")
        box.columnconfigure(1, weight=1)
        for row, (name, label) in enumerate(DETAIL_FIELDS):
            ttk.Label(box, text=label + ":").grid(row=row, column=0, sticky="w")
            entry = ttk.Entry(
                box, textvariable=self.detail_vars[name], state="readonly"
            )
            entry.grid(row=row, column=1, sticky="ew", padx=PAD, pady=2)
            self.detail_entries[name] = entry
            ttk.Button(
                box,
                text="Copy",
                width=6,
                command=partial(self.copy_field, name, label),
            ).grid(row=row, column=2, pady=2)
        ttk.Checkbutton(
            box,
            text="Show key and serial",
            variable=self.show_secrets,
            command=self._apply_secret_visibility,
        ).grid(row=len(DETAIL_FIELDS), column=1, sticky="w", pady=(PAD, 0))
        self._apply_secret_visibility()

    # --- behaviour ----------------------------------------------------------

    def refresh(self, select_id: str | None = None) -> None:
        """Reload the table from the DB, keeping or setting the selection."""
        keep = select_id or (self.selected.id if self.selected else None)
        self.tree.delete(*self.tree.get_children())
        for lic in list_licences(self.con, self.search_var.get()):
            self.tree.insert("", "end", iid=lic.id, values=row_values(lic))
        if keep and self.tree.exists(keep):
            # Show it now rather than waiting for the queued select event, so
            # the details are current as soon as refresh returns.
            self._show(get_licence(self.con, keep))
            self.tree.selection_set(keep)
            self.tree.see(keep)
        else:
            self._show(None)

    def _on_select(self) -> None:
        selection = self.tree.selection()
        if not selection:
            self._show(None)
        elif self.selected is None or selection[0] != self.selected.id:
            self._show(get_licence(self.con, selection[0]))

    def _show(self, lic: Licence | None) -> None:
        self.selected = lic
        state = "normal" if lic else "disabled"
        self.edit_button.configure(state=state)
        self.delete_button.configure(state=state)
        for name, _ in DETAIL_FIELDS:
            value = getattr(lic, name) if lic else None
            self.detail_vars[name].set(value or "")

    def _apply_secret_visibility(self) -> None:
        show = "" if self.show_secrets.get() else MASK
        for name in SECRET_FIELDS:
            self.detail_entries[name].configure(show=show)

    def copy_field(self, name: str, label: str) -> None:
        """Put one field of the selected licence on the clipboard."""
        lic = self.selected
        value = getattr(lic, name) if lic else None
        if lic is None:
            self.status_var.set("Select a licence first.")
            return
        if not value:
            self.status_var.set(f"{lic.product_name} has no {label.lower()}.")
            return
        self.clipboard_clear()
        self.clipboard_append(value)
        self.update()  # hand the data to the OS clipboard now
        self.status_var.set(copied_message(label, lic))

    def _write[T](self, action: Callable[[], T]) -> tuple[bool, T | None]:
        """Run a store write and commit it; roll back on any failure.

        Returns (True, result), or (False, None) after showing a LicenceError
        (an expected refusal, e.g. a duplicate). Other errors propagate to
        Tk's error reporting after the rollback.
        """
        try:
            result = action()
            self.con.commit()
            return True, result
        except LicenceError as e:
            self.con.rollback()
            self.show_error(self, str(e))
            return False, None
        except Exception:
            self.con.rollback()
            raise

    def _edit_loop(
        self,
        title: str,
        initial: LicenceFields | None,
        save: Callable[[LicenceFields], str],
    ) -> tuple[str, LicenceFields] | None:
        """Ask until the fields save or the user cancels.

        After a refusal the form reopens with what the user typed.
        Returns (licence id, fields) on success.
        """
        fields = initial
        while (fields := self.ask(self, title, fields)) is not None:
            ok, licence_id = self._write(partial(save, fields))
            if ok and licence_id is not None:
                return licence_id, fields
        return None

    def add(self) -> None:
        done = self._edit_loop("Add licence", None, lambda f: add_licence(self.con, f))
        if done:
            licence_id, fields = done
            self.search_var.set("")  # make sure the new licence is visible
            self.refresh(select_id=licence_id)
            self.status_var.set(f"Added licence for {fields.product_name}.")

    def edit(self) -> None:
        current = self.selected
        if current is None:
            return
        done = self._edit_loop(
            "Edit licence",
            current.fields,
            lambda f: update_licence(self.con, current.id, f),
        )
        if done:
            licence_id, fields = done
            self.selected = None  # the id may have changed
            self.refresh(select_id=licence_id)
            self.status_var.set(f"Saved licence for {fields.product_name}.")

    def delete(self) -> None:
        current = self.selected
        if current is None:
            return
        if not self.confirm(self, f"Delete the licence for {current.product_name}?"):
            return
        ok, _ = self._write(partial(delete_licence, self.con, current.id))
        if ok:
            self.selected = None
            self.refresh()
            self.status_var.set(f"Deleted licence for {current.product_name}.")
