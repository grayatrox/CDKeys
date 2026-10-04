"""The settings file (``settings.toml``) holding the database path."""

from __future__ import annotations

import json
import logging
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


# The checkout this package runs from: src/cdkeys/settings.py -> repo root.
# launch.py and tasks.py both install the package editable (pip install -e),
# so __file__ is always inside the checkout, never in site-packages.
PROJECT_DIR = Path(__file__).resolve().parents[2]

logger = logging.getLogger(__name__)


def settings_path(project_dir: Path = PROJECT_DIR) -> Path:
    """
    Location of the settings file: ``settings.toml`` in the project directory.

    It lives in the checkout (and is gitignored) rather than in %APPDATA%,
    because the checkout is backed up and %APPDATA% is not.
    """
    return project_dir / SETTINGS_FILENAME


def user_config_dir(
    environ: Mapping[str, str],
    platform: str = sys.platform,
    home: Path | None = None,
) -> Path:
    """
    The per-user config folder (the GUI log is kept here).

    Windows: %APPDATA%\\cdkeys. Elsewhere: $XDG_CONFIG_HOME/cdkeys,
    defaulting to ~/.config.
    """
    home = Path.home() if home is None else home
    if platform == "win32":
        appdata = environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
    else:
        xdg = environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else home / ".config"
    return base / APP_DIR


def legacy_settings_path(
    environ: Mapping[str, str],
    platform: str = sys.platform,
    home: Path | None = None,
) -> Path:
    """Where the settings file lived before it moved to the project directory."""
    return user_config_dir(environ, platform, home) / SETTINGS_FILENAME


def locate_settings(
    environ: Mapping[str, str],
    project_dir: Path = PROJECT_DIR,
    platform: str = sys.platform,
    home: Path | None = None,
) -> Path:
    """
    The settings file to use, moving an older per-user one into place first.

    If the project has no settings file but the legacy per-user one exists,
    its ``db_path`` is written to the project file as an absolute path (a
    relative one was relative to the legacy folder). The legacy file is left
    as it is, and an existing project file is never overwritten.
    Raises SettingsError, naming the legacy file, if that file is invalid.
    """
    settings_file = settings_path(project_dir)
    legacy_file = legacy_settings_path(environ, platform, home)
    if not settings_file.exists() and legacy_file.exists():
        save_db_path(settings_file, load_settings(legacy_file).db_path)
        logger.info("Copied settings from %s to %s", legacy_file, settings_file)
    return settings_file


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
