#!/usr/bin/env python3
"""Cross-platform helper (Windows / macOS / Linux). Needs only Python 3.11+.

  python tools/dev.py setup   create .venv and install requirements
  python tools/dev.py run     start the GUI
  python tools/dev.py build   build standalone app into dist/ (.exe on Windows, .app on macOS)
"""
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def sh(*args):
    subprocess.check_call([str(a) for a in args], cwd=ROOT)


def setup():
    if sys.version_info < (3, 11):
        sys.exit("Python 3.11+ required (on macOS use python.org, not the system Python)")
    if not PY.exists():
        venv.EnvBuilder(with_pip=True).create(VENV)
    sh(PY, "-m", "pip", "install", "--upgrade", "pip")
    sh(PY, "-m", "pip", "install", "-r", "requirements.txt")
    print("\nDone. Start the GUI with:  python tools/dev.py run")


def need_venv():
    if not PY.exists():
        setup()


def run():
    need_venv()
    sh(PY, "gui/app.py")


def build():
    need_venv()
    sh(PY, "-m", "PyInstaller", "--noconfirm", "--windowed", "--name", "MidiFootswitch", "gui/app.py")
    print("\nOutput in dist/")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"setup": setup, "run": run, "build": build}.get(cmd, lambda: sys.exit(__doc__))()
