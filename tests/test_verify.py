from pathlib import Path

import pytest

from cdkeys.db import ensure_schema, open_db
from cdkeys.verify import test_plaintext_access as check_plaintext_access


def test_plaintext_check_releases_the_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "keys.sqlite3"
    con = open_db(db, "pw", create=True)
    ensure_schema(con)
    con.commit()
    con.close()

    check_plaintext_access(db)

    assert "OK: Plain sqlite3 cannot read DB." in capsys.readouterr().out
    # Windows refuses to delete a file that still has an open handle.
    db.unlink()
    assert not db.exists()
