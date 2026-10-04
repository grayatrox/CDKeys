from pathlib import Path

import pytest

from cdkeys.db import CDKeysDBError
from cdkeys.settings import (
    PROJECT_DIR,
    SettingsError,
    legacy_settings_path,
    load_settings,
    locate_settings,
    resolve_db_path,
    save_db_path,
    settings_path,
)


def test_settings_file_is_in_the_project_directory() -> None:
    # The checkout is backed up; %APPDATA% is not.
    assert settings_path() == PROJECT_DIR / "settings.toml"
    assert (PROJECT_DIR / "pyproject.toml").is_file()
    assert (PROJECT_DIR / "src" / "cdkeys" / "settings.py").is_file()


def _legacy_env(tmp_path: Path) -> dict[str, str]:
    return {"APPDATA": str(tmp_path / "appdata"), "XDG_CONFIG_HOME": str(tmp_path)}


def _locate(tmp_path: Path) -> Path:
    return locate_settings(
        _legacy_env(tmp_path), project_dir=tmp_path / "proj", home=tmp_path
    )


def _legacy(tmp_path: Path) -> Path:
    return legacy_settings_path(_legacy_env(tmp_path), home=tmp_path)


def test_locate_without_any_settings_returns_project_path(tmp_path: Path) -> None:
    (tmp_path / "proj").mkdir()

    found = _locate(tmp_path)

    assert found == tmp_path / "proj" / "settings.toml"
    assert not found.exists()


def test_legacy_settings_are_copied_into_the_project(tmp_path: Path) -> None:
    (tmp_path / "proj").mkdir()
    save_db_path(_legacy(tmp_path), tmp_path / "keys.sqlite3")

    found = _locate(tmp_path)

    assert found == tmp_path / "proj" / "settings.toml"
    assert load_settings(found).db_path == tmp_path / "keys.sqlite3"
    assert _legacy(tmp_path).exists()  # left in place, never deleted


def test_relative_legacy_db_path_keeps_pointing_at_the_same_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "proj").mkdir()
    legacy = _legacy(tmp_path)
    legacy.parent.mkdir(parents=True)
    legacy.write_text("db_path = 'keys.sqlite3'\n", encoding="utf-8")

    found = _locate(tmp_path)

    assert load_settings(found).db_path == legacy.parent / "keys.sqlite3"


def test_existing_project_settings_are_never_overwritten(tmp_path: Path) -> None:
    project_file = tmp_path / "proj" / "settings.toml"
    save_db_path(project_file, tmp_path / "project.sqlite3")
    save_db_path(_legacy(tmp_path), tmp_path / "legacy.sqlite3")

    found = _locate(tmp_path)

    assert load_settings(found).db_path == tmp_path / "project.sqlite3"


def test_invalid_legacy_settings_fail_naming_the_file(tmp_path: Path) -> None:
    (tmp_path / "proj").mkdir()
    legacy = _legacy(tmp_path)
    legacy.parent.mkdir(parents=True)
    legacy.write_text("db_path = 5\n", encoding="utf-8")

    with pytest.raises(SettingsError, match="'db_path' must be a string") as err:
        _locate(tmp_path)

    assert str(legacy) in str(err.value)
    assert not (tmp_path / "proj" / "settings.toml").exists()


def test_windows_legacy_path_is_under_appdata(tmp_path: Path) -> None:
    path = legacy_settings_path(
        {"APPDATA": str(tmp_path)}, platform="win32", home=tmp_path
    )

    assert path == tmp_path / "cdkeys" / "settings.toml"


def test_windows_without_appdata_falls_back_to_roaming(tmp_path: Path) -> None:
    path = legacy_settings_path({}, platform="win32", home=tmp_path)

    assert path == tmp_path / "AppData" / "Roaming" / "cdkeys" / "settings.toml"


def test_posix_uses_xdg_config_home(tmp_path: Path) -> None:
    env = {"XDG_CONFIG_HOME": str(tmp_path / "cfg")}

    path = legacy_settings_path(env, platform="linux", home=tmp_path)

    assert path == tmp_path / "cfg" / "cdkeys" / "settings.toml"


def test_posix_defaults_to_dot_config(tmp_path: Path) -> None:
    path = legacy_settings_path({}, platform="linux", home=tmp_path)

    assert path == tmp_path / ".config" / "cdkeys" / "settings.toml"


@pytest.mark.parametrize(
    "db_path",
    [
        r"C:\Users\me\OneDrive\cd_keys_encrypted.sqlite3",
        r"C:\My Keys\it's \"quoted\"\keys.sqlite3",
        str(Path.home() / "Schlüssel" / "keys ü.sqlite3"),
    ],
)
def test_save_then_load_round_trips(tmp_path: Path, db_path: str) -> None:
    settings_file = tmp_path / "nested" / "settings.toml"

    save_db_path(settings_file, Path(db_path))

    assert load_settings(settings_file).db_path == Path(db_path)


def test_relative_db_path_is_relative_to_the_settings_file(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text("db_path = 'keys.sqlite3'\n", encoding="utf-8")

    assert load_settings(settings_file).db_path == tmp_path / "keys.sqlite3"


def test_home_is_expanded(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text("db_path = '~/keys.sqlite3'\n", encoding="utf-8")

    assert load_settings(settings_file).db_path == Path.home() / "keys.sqlite3"


def test_missing_file_explains_what_to_create(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.toml"

    with pytest.raises(SettingsError) as err:
        load_settings(settings_file)

    assert str(settings_file) in str(err.value)
    assert "db_path = " in str(err.value)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("db_path = \n", "not valid TOML"),
        ("other = 'x'\n", "missing 'db_path'"),
        ("db_path = 5\n", "'db_path' must be a string"),
        ("db_path = '   '\n", "missing 'db_path'"),
    ],
)
def test_bad_settings_are_rejected(tmp_path: Path, content: str, message: str) -> None:
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(content, encoding="utf-8")

    with pytest.raises(SettingsError, match=message):
        load_settings(settings_file)


def test_cli_value_wins_over_settings_file(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.toml"
    save_db_path(settings_file, tmp_path / "from-settings.sqlite3")

    assert resolve_db_path("cli.sqlite3", settings_file) == Path("cli.sqlite3")


def test_settings_file_used_without_cli_value(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.toml"
    save_db_path(settings_file, tmp_path / "from-settings.sqlite3")

    assert resolve_db_path(None, settings_file) == tmp_path / "from-settings.sqlite3"


def test_cli_value_does_not_need_a_settings_file(tmp_path: Path) -> None:
    assert resolve_db_path("~/x.sqlite3", tmp_path / "absent.toml") == (
        Path.home() / "x.sqlite3"
    )


def test_blank_cli_value_falls_through_to_error(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="--db"):
        resolve_db_path("  ", tmp_path / "absent.toml")


def test_settings_error_is_a_cdkeys_error() -> None:
    # Callers already turn CDKeysDBError into a clean exit message.
    assert issubclass(SettingsError, CDKeysDBError)
