"""Startup safety: back up the database, then apply migrations."""

import re
import sqlite3
from datetime import datetime
from pathlib import Path

from competence.core.db import connect, migrate

KEEP = 20


def backup_database(db: Path, backups: Path, keep: int = KEEP) -> Path:
    """Copy db to backups/<stem>-YYYYMMDD-HHMMSS.sqlite with SQLite's online backup API.

    Keeps the newest `keep` backups for this database and deletes older ones. Only files named the way this
    function names them are rotated: hand-kept copies and other databases' backups are left alone.
    """
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target, n = backups / f"{db.stem}-{stamp}.sqlite", 2
    while target.exists():
        target, n = backups / f"{db.stem}-{stamp}-{n}.sqlite", n + 1
    src, dst = sqlite3.connect(db), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    ours = re.compile(rf"{re.escape(db.stem)}-\d{{8}}-\d{{6}}(-\d+)?\.sqlite")
    old = sorted((p for p in backups.iterdir() if ours.fullmatch(p.name)), key=lambda p: p.stat().st_mtime_ns)
    for p in old[:-keep]:
        p.unlink()
    return target


def prepare_database(db: Path, backups: Path) -> Path:
    """Back up an existing database, then migrate it. Returns the backup path.

    Never creates a database: a missing file means `competence init` hasn't been run (or the path is wrong).
    """
    if not db.exists():
        raise FileNotFoundError(
            f"{db} does not exist. Create it first with: competence init" + (" --demo" if "demo" in db.stem else "")
        )
    taken = backup_database(db, backups)
    conn = connect(db)
    try:
        migrate(conn)
    finally:
        conn.close()
    return taken
