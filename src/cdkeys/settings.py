"""Per-user settings file (``settings.toml``) holding the database path."""

from __future__ import annotations

import json
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from cdkeys.db import CDKeysDBError

APP_DIR = "cdkeys"
SETTINGS_FILENAME = "settings.toml"


class SettingsError(CDKeysDBError):
    """The settings file is missing, unreadable or invalid."""


@dataclass(frozen=True)
class Settings:
    db_path: Path


def settings_path(
    environ: Mapping[str, str],
    platform: str = sys.platform,
    home: Path | None = None,
) -> Path:
    """
    Location of the settings file for the current user.

    Windows: %APPDATA%\\cdkeys\\settings.toml. Elsewhere:
    $XDG_CONFIG_HOME/cdkeys/settings.toml, defaulting to ~/.config.
    """
    home = Path.home() if home is None else home
    if platform == "win32":
        appdata = environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
    else:
        xdg = environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else home / ".config"
    return base / APP_DIR / SETTINGS_FILENAME


def load_settings(settings_file: Path) -> Settings:
    """
    Read and validate the settings file.

    ``db_path`` may use ``~``; a relative path is taken relative to the
    settings file's directory, so it never depends on the working directory.
    Raises SettingsError naming the file and the problem.
    """
    try:
        raw = settings_file.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise SettingsError(
            f"No settings file at {settings_file}. Create it containing:\n"
            "  db_path = 'C:\\path\\to\\cd_keys_encrypted.sqlite3'\n"
            "(see settings.example.toml), or pass --db PATH."
        ) from e
    except OSError as e:
        raise SettingsError(f"{settings_file}: cannot read: {e}") from e

    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as e:
        raise SettingsError(f"{settings_file}: not valid TOML: {e}") from e

    value = data.get("db_path")
    if value is not None and not isinstance(value, str):
        raise SettingsError(f"{settings_file}: 'db_path' must be a string")
    if value is None or not value.strip():
        raise SettingsError(f"{settings_file}: missing 'db_path'")

    db_path = Path(value.strip()).expanduser()
    if not db_path.is_absolute():
        db_path = settings_file.parent / db_path
    return Settings(db_path=db_path)


def save_db_path(settings_file: Path, db_path: Path) -> None:
    """Write ``db_path`` to the settings file, creating its directory.

    The file currently holds only db_path, so it is rewritten whole.
    """
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    # JSON string escapes (\\, \", \uXXXX, ...) are all valid TOML basic-string
    # escapes, so this quotes any path safely without a TOML writer dependency.
    settings_file.write_text(
        f"db_path = {json.dumps(str(db_path), ensure_ascii=False)}\n",
        encoding="utf-8",
    )


def resolve_db_path(cli_value: str | None, settings_file: Path) -> Path:
    """The database path: a non-blank ``--db`` value, else the settings file."""
    if cli_value is not None and cli_value.strip():
        return Path(cli_value.strip()).expanduser()
    try:
        return load_settings(settings_file).db_path
    except SettingsError as e:
        if cli_value is not None:
            raise SettingsError(f"--db is blank. {e}") from e
        raise
