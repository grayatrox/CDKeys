#!/usr/bin/env python3
"""
Run cdkeys, installing its dependencies on first start.

    python launch.py add entries.local.json [--db PATH]
    python launch.py verify [--db PATH]

Standard library only, so it runs on a bare Python 3.12+. The first start (or
the first start after requirements-runtime.lock or pyproject.toml change)
creates .venv, installs the pinned runtime dependencies and the package, and
records a stamp; later starts go straight to ``python -m cdkeys`` in .venv.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

MIN_PYTHON = (3, 12)
ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
STAMP = VENV / ".cdkeys-install-stamp"
# The stamp covers everything that decides what gets installed.
STAMP_INPUTS = ("requirements-runtime.lock", "pyproject.toml")

Runner = Callable[[Sequence[str]], int]


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def expected_stamp(root: Path) -> str:
    """Digest of the files that determine the installed environment."""
    digest = hashlib.sha256()
    for name in STAMP_INPUTS:
        digest.update(name.encode() + b"\0" + (root / name).read_bytes() + b"\0")
    return digest.hexdigest()


def needs_install(venv: Path, stamp: Path, expected: str) -> bool:
    if not venv_python(venv).exists():
        return True
    try:
        return stamp.read_text(encoding="utf-8").strip() != expected
    except OSError:
        return True


def install(root: Path, venv: Path, stamp: Path, run: Runner) -> None:
    """Create the venv and install runtime deps + package; stamp on success.

    The stamp is removed first and written last, so an interrupted or failed
    install is retried on the next start. Raises SystemExit on failure.
    """
    stamp.unlink(missing_ok=True)
    python = str(venv_python(venv))
    steps: list[list[str]] = []
    if not venv_python(venv).exists():
        steps.append([sys.executable, "-m", "venv", str(venv)])
    steps += [
        [
            python,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-q",
            "-r",
            str(root / "requirements-runtime.lock"),
        ],
        [
            python,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-q",
            "--no-deps",
            "-e",
            str(root),
        ],
    ]
    for cmd in steps:
        rc = run(cmd)
        if rc != 0:
            raise SystemExit(
                f"First-start install failed (exit {rc}) running: {' '.join(cmd)}\n"
                "Fix the problem above and run again; the install will retry."
            )
    stamp.write_text(expected_stamp(root) + "\n", encoding="utf-8")


def _run(cmd: Sequence[str]) -> int:
    # Commands are built from this file's constants plus the user's own CLI
    # arguments; there is no shell and no external input.
    return subprocess.run(list(cmd), cwd=ROOT).returncode  # noqa: S603


def main(argv: list[str], run: Runner = _run) -> int:
    if sys.version_info < MIN_PYTHON:
        print(
            f"cdkeys needs Python {'.'.join(map(str, MIN_PYTHON))}+; "
            f"this is {sys.version.split()[0]}.",
            file=sys.stderr,
        )
        return 1
    if needs_install(VENV, STAMP, expected_stamp(ROOT)):
        print("First start: installing dependencies into .venv ...", flush=True)
        install(ROOT, VENV, STAMP, run)
    return run([str(venv_python(VENV)), "-m", "cdkeys", *argv])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
