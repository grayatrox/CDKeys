# CDKeys

An encrypted store for software licence keys. Records live in a
[SQLCipher](https://www.zetetic.net/sqlcipher/) database (an encrypted SQLite
file), so the file is unreadable without its passphrase.

It is a Python package (`src/cdkeys`) with one command and three subcommands:

- `cdkeys gui` opens a desktop key manager: browse, search, add, edit and
  delete licences, and copy any field to the clipboard.
- `cdkeys add` adds or updates licences from a JSON file.
- `cdkeys verify` checks that the database opens, shows counts per product,
  flags product names that differ only by case, and confirms that plain SQLite
  cannot read the file.

Each licence's primary key is a SHA-256 digest of the product name plus its key,
serial number and login, or of a manual `identity` such as an invoice number.
Matching ignores case and surrounding whitespace. Re-importing the same licence
updates the existing record instead of duplicating it. `assigned_device` and
`notes` are not part of the identity and can change freely.

> The identity format (`v1`) must never change. Existing records are keyed by
> it, and `tests/test_license_identity.py` fails if it does.

## Install

Requires Python 3.12 or newer from python.org (see `.python-version`). There
is no separate install step: **double-click `CD Key Manager.pyw`**. No console
window opens. The first start shows a small "Setting up CD Key Manager" window
while it installs what it needs, then opens the key manager. If setup fails,
a message box shows why, and the next start tries again.

The first run creates `.venv` and installs the pinned runtime dependency
(`requirements-runtime.lock`) and the package. Later runs skip this. It
installs again automatically if `requirements-runtime.lock` or `pyproject.toml`
changes, and retries on the next start if an install fails. `sqlcipher3` ships
a Windows wheel with SQLCipher bundled, so no compiler is needed.

For development (adds ruff, mypy and pytest from `requirements.lock`):

```powershell
python tasks.py setup
```

## Run

**Key manager window:** double-click `CD Key Manager.pyw`. (Inside the venv
it is also `cdkeys-gui`, which opens no console either.)

On first start it asks whether to open an existing database or create a new
one, and saves the choice to the settings file (see [Configure](#configure)).
It then asks for the passphrase; a new database asks for it twice.

- **Search** filters as you type, across every field.
- The table masks keys and serials down to their last four characters. The
  details pane on the right shows the selected licence, with **Copy** beside
  each field. Tick **Show key and serial** to reveal them there. The status bar
  says what was copied, never the value.
- **Add…** opens a form. **Edit…** (or double-click) changes the selected
  licence; editing the product, key, serial, login or identity re-keys it.
  **Delete** (or the Delete key) asks for confirmation first. Each change is
  saved immediately.
- A licence needs a product name plus a product key, serial number or login.
  If it has none of those, give it an identity such as an invoice number.
  Adding a licence that is already stored is refused, not merged.

**Add or update licences in bulk from a file:**

Point cdkeys at your database first (see [Configure](#configure)).

1. Copy `entries.example.json` to `entries.local.json` and fill it in. Any
   `entries*.json` file other than the example is git-ignored, because it holds
   plaintext keys.
2. Run:

   ```powershell
   python launch.py add entries.local.json
   ```

   Every entry is validated before the database is opened. The run shows a
   preview with keys and serials redacted, then upserts.
3. Delete `entries.local.json`.

Each entry needs `product_name` plus at least one of `product_key`,
`serial_number`, `associated_login` or `identity`. The other allowed fields
are `assigned_device` and `notes`.

If the database file does not exist, `add` asks before creating one and
asks for the new passphrase twice. A wrong passphrase or a missing file exits
with an error.

**Verify the database:**

```powershell
python launch.py verify
```

Inside the venv the same commands are available as `cdkeys add ...` /
`cdkeys verify`, or `python -m cdkeys ...`.

## Test

```powershell
python tasks.py test     # pytest only
python tasks.py check    # format check, lint, strict type check and tests (the CI gate)
```

The suite runs offline in a few seconds against temporary databases. It never
touches your real database. `python tasks.py fmt` reformats the code;
`python tasks.py lint` and `python tasks.py typecheck` run those checks on
their own.

## Configure

The database path lives in a per-user settings file, outside the repository:

- Windows: `%APPDATA%\cdkeys\settings.toml`
- Linux/macOS: `~/.config/cdkeys/settings.toml` (or under `$XDG_CONFIG_HOME`)

```toml
db_path = 'C:\Users\you\OneDrive\cd_keys_encrypted.sqlite3'
```

Use single quotes so backslashes stay literal. A relative path is taken
relative to the settings file's folder. `settings.example.toml` is a template.
There is no built-in default: if the file or `db_path` is missing, the CLI says
where to create it. `--db PATH` on either subcommand overrides the file for one
run.

The cipher settings are fixed at the SQLCipher 4 defaults, which the existing
database was created with. Opening it with any other key-derivation settings
fails. Strengthening them would need a `PRAGMA rekey` / `sqlcipher_export`
migration.
