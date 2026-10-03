#!/usr/bin/env python3
"""
Project task runner (stdlib only, works on Windows without make/just).

    python tasks.py setup       create .venv and install the locked environment
    python tasks.py fmt         format the code (writes files)
    python tasks.py lint        lint (check only, never fixes)
    python tasks.py typecheck   mypy, strict
    python tasks.py test        pytest
    python tasks.py check       fmt --check + lint + typecheck + test (CI)
    python tasks.py run upsert ENTRIES.json [--db PATH]
    python tasks.py run verify [--db PATH]
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VENV_PYTHON = VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
RUN_TARGETS = {"upsert": "upsert_licenses.py", "verify": "verify_cdkeys_db.py"}


def _run(*cmd: str | Path) -> int:
    print("+ " + " ".join(str(c) for c in cmd), flush=True)
    # Arguments are this file's constants plus the developer's own CLI args;
    # there is no shell and no external input to sanitise.
    return subprocess.run([str(c) for c in cmd], cwd=ROOT).returncode  # noqa: S603


def _venv(*args: str) -> int:
    if not VENV_PYTHON.exists():
        print("No .venv found; run: python tasks.py setup", file=sys.stderr)
        return 1
    return _run(VENV_PYTHON, *args)


def setup(_args: list[str]) -> int:
    if not VENV_PYTHON.exists():
        rc = _run(sys.executable, "-m", "venv", VENV)
        if rc:
            return rc
    return _venv("-m", "pip", "install", "-r", "requirements.lock") or _venv(
        "-m", "pip", "install", "-e", ".", "--no-deps"
    )


def fmt(_args: list[str]) -> int:
    return _venv("-m", "ruff", "format", ".")


def lint(_args: list[str]) -> int:
    return _venv("-m", "ruff", "check", ".")


def typecheck(_args: list[str]) -> int:
    return _venv("-m", "mypy")


def test(args: list[str]) -> int:
    return _venv("-m", "pytest", *args)


def check(_args: list[str]) -> int:
    return (
        _venv("-m", "ruff", "format", "--check", ".")
        or lint([])
        or typecheck([])
        or test([])
    )


def run(args: list[str]) -> int:
    if not args or args[0] not in RUN_TARGETS:
        print(f"usage: python tasks.py run {{{','.join(RUN_TARGETS)}}} [args...]")
        return 2
    return _venv(RUN_TARGETS[args[0]], *args[1:])


TASKS: dict[str, Callable[[list[str]], int]] = {
    "setup": setup,
    "fmt": fmt,
    "lint": lint,
    "typecheck": typecheck,
    "test": test,
    "check": check,
    "run": run,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in TASKS:
        print(__doc__)
        return 2
    return TASKS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
