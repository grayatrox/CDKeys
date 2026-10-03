# CDKeys

An encrypted store for software licence keys. Records live in a
[SQLCipher](https://www.zetetic.net/sqlcipher/) database (an encrypted SQLite
file), so the file is unreadable without its passphrase.

- `upsert_licenses.py` adds or updates licences from a JSON file.
- `verify_cdkeys_db.py` checks that the database opens, shows counts per
  product, flags product names that differ only by case, and confirms that
  plain SQLite cannot read the file.

Each licence's primary key is a SHA-256 digest of the product name plus its key,
serial number and login, or of a manual `identity` such as an invoice number.
Matching ignores case and surrounding whitespace. Re-importing the same licence
updates the existing record instead of duplicating it. `assigned_device` and
`notes` are not part of the identity and can change freely.

> The identity format (`v1`) must never change. Existing records are keyed by
> it, and `tests/test_license_identity.py` fails if it does.

## Install

Requires Python 3.12 (see `.python-version`). `sqlcipher3` ships a Windows
wheel with SQLCipher bundled, so no compiler is needed.

```powershell
python tasks.py setup
```

This creates `.venv` and installs the exact versions in `requirements.lock`.

## Run

Point the scripts at your database first (see [Configure](#configure)).

**Add or update licences:**

1. Copy `entries.example.json` to `entries.local.json` and fill it in. Any
   `entries*.json` file other than the example is git-ignored, because it holds
   plaintext keys.
2. Run:

   ```powershell
   python tasks.py run upsert entries.local.json
   ```

   Every entry is validated before the database is opened. The run shows a
   preview with keys and serials redacted, then upserts.
3. Delete `entries.local.json`.

Each entry needs `product_name` plus at least one of `product_key`,
`serial_number`, `associated_login` or `identity`. The other allowed fields
are `assigned_device` and `notes`.

If the database file does not exist, the script asks before creating one and
asks for the new passphrase twice. A wrong passphrase or a missing file exits
with an error.

**Verify the database:**

```powershell
python tasks.py run verify
```

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

| Setting | How to set | Notes |
|---|---|---|
| Database path | `--db PATH` on either script, or the `CDKEYS_DB` environment variable | Required. There is no built-in default. `--db` wins over `CDKEYS_DB`. |

To set it permanently on Windows (takes effect in new terminals):

```powershell
setx CDKEYS_DB "C:\path\to\cd_keys_encrypted.sqlite3"
```

`.env.example` documents the variable. The scripts read the process
environment and do not load `.env` files.

The cipher settings are fixed at the SQLCipher 4 defaults, which the existing
database was created with. Opening it with any other key-derivation settings
fails. Strengthening them would need a `PRAGMA rekey` / `sqlcipher_export`
migration.
