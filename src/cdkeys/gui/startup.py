"""GUI startup: find the database (settings or first-run choice) and unlock it."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from cdkeys.db import DBConn, WrongPassphraseError, ensure_schema, open_db
from cdkeys.settings import SettingsError, load_settings, save_db_path

DB_FILETYPES = [("SQLCipher database", "*.sqlite3 *.db"), ("All files", "*.*")]


@dataclass(frozen=True)
class UseDatabase:
    path: Path
    create: bool  # the file does not exist yet


@dataclass(frozen=True)
class ChooseDatabase:
    """No settings file yet: ask the user to open or create a database."""


@dataclass(frozen=True)
class StartupError:
    message: str


Plan = UseDatabase | ChooseDatabase | StartupError


def plan_startup(cli_value: str | None, settings_file: Path) -> Plan:
    """Decide how to start, without any UI.

    An existing but invalid settings file is an error rather than a prompt,
    so the GUI never overwrites a file the user wrote by hand.
    """
    if cli_value is not None and cli_value.strip():
        path = Path(cli_value.strip()).expanduser()
    elif not settings_file.exists():
        return ChooseDatabase()
    else:
        try:
            path = load_settings(settings_file).db_path
        except SettingsError as e:
            return StartupError(str(e))
    return UseDatabase(path=path, create=not path.exists())


def resolve_plan(root: tk.Misc, plan: Plan, settings_file: Path) -> UseDatabase | None:
    """Turn a plan into a database location, asking the user where needed.

    Returns None if the user cancels or startup cannot continue.
    """
    if isinstance(plan, StartupError):
        messagebox.showerror("CD Key Manager", plan.message, parent=root)
        return None
    if isinstance(plan, ChooseDatabase):
        answer = messagebox.askyesnocancel(
            "CD Key Manager",
            "No database is configured yet.\n\n"
            "Yes: open an existing database file.\n"
            "No: create a new encrypted database.\n"
            "Cancel: quit.",
            parent=root,
        )
        if answer is None:
            return None
        chosen = (
            filedialog.askopenfilename(
                parent=root, title="Open database", filetypes=DB_FILETYPES
            )
            if answer
            else filedialog.asksaveasfilename(
                parent=root,
                title="Create database",
                defaultextension=".sqlite3",
                filetypes=DB_FILETYPES,
            )
        )
        if not chosen:
            return None
        save_db_path(settings_file, Path(chosen))
        return UseDatabase(path=Path(chosen), create=not Path(chosen).exists())
    if plan.create and not messagebox.askyesno(
        "CD Key Manager",
        f"No database found at:\n{plan.path}\n\nCreate a new encrypted database there?",
        parent=root,
    ):
        return None
    return plan


class PassphraseDialog(simpledialog.Dialog):
    """Masked passphrase entry; asks twice when creating a database."""

    def __init__(self, parent: tk.Misc, db_path: Path, confirm: bool) -> None:
        self.db_path = db_path
        self.confirm = confirm
        self.passphrase: str | None = None
        super().__init__(parent, "Unlock database" if not confirm else "New database")

    def body(self, master: tk.Frame) -> tk.Widget:
        ttk.Label(master, text=str(self.db_path)).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )
        ttk.Label(master, text="Passphrase:").grid(row=1, column=0, sticky="w")
        self.first = ttk.Entry(master, show="•", width=36)
        self.first.grid(row=1, column=1, pady=2)
        if self.confirm:
            ttk.Label(master, text="Repeat:").grid(row=2, column=0, sticky="w")
            self.second = ttk.Entry(master, show="•", width=36)
            self.second.grid(row=2, column=1, pady=2)
        return self.first

    def validate(self) -> bool:
        value = self.first.get()
        if not value:
            messagebox.showwarning("Passphrase", "Enter a passphrase.", parent=self)
            return False
        if self.confirm and self.second.get() != value:
            messagebox.showwarning(
                "Passphrase", "The passphrases do not match.", parent=self
            )
            return False
        return True

    def apply(self) -> None:
        self.passphrase = self.first.get()


def unlock(root: tk.Misc, target: UseDatabase) -> DBConn | None:
    """Ask for the passphrase until the database opens; None if cancelled."""
    while True:
        dialog = PassphraseDialog(root, target.path, confirm=target.create)
        if dialog.passphrase is None:
            return None
        try:
            con = open_db(target.path, dialog.passphrase, create=target.create)
        except WrongPassphraseError:
            messagebox.showerror(
                "Unlock database", "Wrong passphrase. Try again.", parent=root
            )
            continue
        ensure_schema(con)
        con.commit()
        return con
