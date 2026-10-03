"""Pure display helpers for the GUI (no tkinter, so they are unit-testable)."""

from __future__ import annotations

from cdkeys.store import Licence

MASK = "•"  # bullet

# (Licence attribute, label) for each field shown with a Copy button.
DETAIL_FIELDS: tuple[tuple[str, str], ...] = (
    ("product_name", "Product"),
    ("product_key", "Product key"),
    ("serial_number", "Serial number"),
    ("associated_login", "Login"),
    ("assigned_device", "Device"),
    ("notes", "Notes"),
    ("identity", "Identity"),
)
SECRET_FIELDS = frozenset({"product_key", "serial_number"})

# (column id, heading) for the licence table.
COLUMNS: tuple[tuple[str, str], ...] = (
    ("product", "Product"),
    ("key", "Product key"),
    ("device", "Device"),
    ("login", "Login"),
    ("notes", "Notes"),
)
NOTES_PREVIEW = 40


def mask_secret(value: str | None, visible: int = 4) -> str:
    """Hide all but the last ``visible`` characters (all of short values).

    Enough to tell keys apart in a list without exposing them on screen.
    """
    if not value:
        return ""
    if len(value) <= visible * 2:
        return MASK * len(value)
    return MASK * (len(value) - visible) + value[-visible:]


def notes_preview(notes: str | None, limit: int = NOTES_PREVIEW) -> str:
    """First line of the notes, shortened for a table cell."""
    if not notes:
        return ""
    first = notes.strip().splitlines()[0] if notes.strip() else ""
    return first if len(first) <= limit else first[: limit - 1] + "…"


def row_values(lic: Licence) -> tuple[str, ...]:
    """Table cells for a licence, in COLUMNS order. Secrets are masked."""
    return (
        lic.product_name,
        mask_secret(lic.product_key or lic.serial_number),
        lic.assigned_device or "",
        lic.associated_login or "",
        notes_preview(lic.notes),
    )


def copied_message(label: str, lic: Licence) -> str:
    """Status-bar text after a copy. Never includes the copied value."""
    return f"Copied {label.lower()} for {lic.product_name} to the clipboard."
