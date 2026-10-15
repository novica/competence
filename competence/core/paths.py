"""Where the databases live. The data folder holds personal data about employees: never commit it.

COMPETENCE_DATA (set by --data) wins. Otherwise a source checkout keeps its gitignored data/ folder, so
existing databases are found, and an installed copy uses the per-user app folder
(e.g. %LOCALAPPDATA%\\competence on Windows).
"""

import os
from pathlib import Path

from platformdirs import user_data_dir

ENV_DATA = "COMPETENCE_DATA"
CHECKOUT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    if env := os.environ.get(ENV_DATA):
        return Path(env)
    if (CHECKOUT / "pyproject.toml").is_file():
        return CHECKOUT / "data"
    return Path(user_data_dir("competence", appauthor=False))


def real_db() -> Path:
    return data_dir() / "matrix.sqlite"


def demo_db() -> Path:
    return data_dir() / "demo.sqlite"


def backups() -> Path:
    return data_dir() / "backups"
