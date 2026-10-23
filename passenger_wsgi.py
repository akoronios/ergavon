"""Entry point for Phusion Passenger (Plesk).

Passenger loads this file from the application root. It switches to the
project's virtualenv interpreter, then hands requests to Django.
"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VENV_PYTHON = BASE_DIR / ".venv" / "bin" / "python"

# Re-run under the virtualenv's Python if Passenger started another one
if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execl(str(VENV_PYTHON), str(VENV_PYTHON), *sys.argv)

sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from config.wsgi import application  # noqa: E402,F401
