"""The key manager main window: search, licence table, details with Copy."""

from __future__ import annotations

import tkinter as tk
from functools import partial
from pathlib import Path
from tkinter import ttk

from cdkeys.db import DBConn
from cdkeys.gui.format import (
    COLUMNS,
    DETAIL_FIELDS,
    MASK,
    SECRET_FIELDS,
    copied_message,
    row_values,
)
from cdkeys.store import Licence, get_licence, list_licences

PAD = 6


class KeyManagerApp(ttk.Frame):
    """Main window contents. The caller owns ``con`` and closes it."""

    def __init__(self, master: tk.Tk, con: DBConn, db_path: Path) -> None:
        super().__init__(master, padding=PAD)
        self.con = con
        self.selected: Licence | None = None
        self.search_var = tk.StringVar(master=self)
        self.status_var = tk.StringVar(master=self, value=f"Database: {db_path}")
        self.show_secrets = tk.BooleanVar(master=self, value=False)
        self.detail_vars = {
            name: tk.StringVar(master=self) for name, _ in DETAIL_FIELDS
        }
        self.detail_entries: dict[str, ttk.Entry] = {}

        master.title("CD Key Manager")
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
            self.tree.selection_set(keep)
            self.tree.see(keep)
        else:
            self._show(None)

    def _on_select(self) -> None:
        selection = self.tree.selection()
        self._show(get_licence(self.con, selection[0]) if selection else None)

    def _show(self, lic: Licence | None) -> None:
        self.selected = lic
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
