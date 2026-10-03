#!/usr/bin/env python3
"""
Run cdkeys, installing its dependencies on first start.

Double-click "CD Key Manager.pyw" to open the key manager: no console, and
the first start shows a setup window while it installs. From a terminal:

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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MIN_PYTHON = (3, 12)
ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
STAMP = VENV / ".cdkeys-install-stamp"
# The stamp covers everything that decides what gets installed.
STAMP_INPUTS = ("requirements-runtime.lock", "pyproject.toml")

APP_NAME = "CD Key Manager"
LOG_TAIL_LINES = 15
# Child processes of a console-less (pythonw) parent would otherwise each
# flash a console window on Windows.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

Runner = Callable[[Sequence[str]], int]


class InstallError(Exception):
    """The first-start install failed; the next start retries it."""


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def venv_pythonw(venv: Path) -> Path:
    """The venv's interpreter that opens no console window (Windows)."""
    return venv / ("Scripts/pythonw.exe" if sys.platform == "win32" else "bin/python")


def app_command(venv: Path) -> list[str]:
    return [str(venv_pythonw(venv)), "-m", "cdkeys.gui"]


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
    install is retried on the next start. Raises InstallError on failure.
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
            raise InstallError(
                f"First-start install failed (exit {rc}) running: {' '.join(cmd)}\n"
                "Fix the problem and start again; the install will retry."
            )
    stamp.write_text(expected_stamp(root) + "\n", encoding="utf-8")


def _run(cmd: Sequence[str]) -> int:
    # Commands are built from this file's constants plus the user's own CLI
    # arguments; there is no shell and no external input.
    return subprocess.run(list(cmd), cwd=ROOT).returncode  # noqa: S603


def quiet_runner(log: list[str]) -> Runner:
    """A runner for console-less starts: no window, output kept in ``log``."""

    def run(cmd: Sequence[str]) -> int:
        # Same command sources as _run; no shell and no external input.
        proc = subprocess.run(  # noqa: S603
            list(cmd),
            cwd=ROOT,
            capture_output=True,
            text=True,
            creationflags=NO_WINDOW,
        )
        log.append(proc.stdout + proc.stderr)
        return proc.returncode

    return run


def failure_message(error: BaseException, log: list[str]) -> str:
    """The error plus the last lines of captured output, for a message box."""
    tail = "".join(log).splitlines()[-LOG_TAIL_LINES:]
    details = "\n".join(tail).strip()
    return f"{error}\n\n{details}" if details else str(error)


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
        try:
            install(ROOT, VENV, STAMP, run)
        except InstallError as e:
            print(e, file=sys.stderr)
            return 1
    return run([str(venv_python(VENV)), "-m", "cdkeys", *argv])


# --- double-click start ------------------------------------------------------


def run_with_progress(task: Callable[[], None]) -> BaseException | None:
    """Run ``task`` on a worker thread behind a small progress window.

    Returns the exception the task raised, or None. The window keeps the
    first start from looking frozen while pip works.
    """
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(f"Setting up {APP_NAME}")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=16)
    frame.pack()
    ttk.Label(
        frame,
        text=f"Setting up {APP_NAME} for first use.\n"
        "This takes about a minute and only happens once.",
    ).pack(anchor="w")
    bar = ttk.Progressbar(frame, mode="indeterminate", length=320)
    bar.pack(pady=(12, 0))
    bar.start(15)
    with ThreadPoolExecutor(max_workers=1) as pool:
        # The future carries any exception back to this (UI) thread.
        future = pool.submit(task)

        def poll() -> None:
            if future.done():
                root.destroy()
            else:
                root.after(100, poll)

        root.after(100, poll)
        root.mainloop()
        return future.exception()


def _start_detached(cmd: list[str]) -> None:
    # The app outlives this launcher; same command sources as _run.
    subprocess.Popen(cmd, cwd=ROOT, creationflags=NO_WINDOW)  # noqa: S603


def _show_error_box(message: str) -> None:
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(APP_NAME, message, parent=root)
    root.destroy()


def gui_main(
    run_setup: Callable[[Callable[[], None]], BaseException | None] = run_with_progress,
    start_app: Callable[[list[str]], None] = _start_detached,
    show_error: Callable[[str], None] = _show_error_box,
    runner_factory: Callable[[list[str]], Runner] = quiet_runner,
) -> int:
    """Double-click entry point: set up if needed, then open the key manager.

    Everything is reported in windows; there is no console to print to.
    """
    if sys.version_info < MIN_PYTHON:
        show_error(
            f"{APP_NAME} needs Python {'.'.join(map(str, MIN_PYTHON))} or newer; "
            f"this is {sys.version.split()[0]}."
        )
        return 1
    if needs_install(VENV, STAMP, expected_stamp(ROOT)):
        log: list[str] = []
        run = runner_factory(log)
        error = run_setup(lambda: install(ROOT, VENV, STAMP, run))
        if error is not None:
            show_error(failure_message(error, log))
            return 1
    start_app(app_command(VENV))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
