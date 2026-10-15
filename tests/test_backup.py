import os

import pytest

from competence.core.backup import backup_database, prepare_database
from competence.core.db import connect


def test_backup_is_a_readable_copy(db_path, tmp_path):
    out = backup_database(db_path, tmp_path / "backups")
    c = connect(out)
    assert c.execute("SELECT COUNT(*) FROM skills").fetchone()[0] == 132
    c.close()


def test_backup_keeps_only_newest(db_path, tmp_path):
    backups = tmp_path / "backups"
    made = [backup_database(db_path, backups, keep=3) for _ in range(5)]
    for i, p in enumerate(made):  # make the order unambiguous on fast filesystems
        if p.exists():
            os.utime(p, ns=(i * 10**9, i * 10**9))
    backup_database(db_path, backups, keep=3)
    assert len(list(backups.glob("matrix-*.sqlite"))) == 3


def test_rotation_leaves_other_files_alone(db_path, tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    kept = [
        backups / "matrix-before-cleanup.sqlite",
        backups / "matrix-old-20260101-120000.sqlite",
        backups / "demo-20260101-120000.sqlite",
    ]
    for p in kept:
        p.write_bytes(b"")
        os.utime(p, ns=(0, 0))
    for _ in range(3):
        backup_database(db_path, backups, keep=1)
    assert all(p.exists() for p in kept)
    assert len(list(backups.glob("matrix-2*.sqlite"))) == 1


def test_prepare_refuses_missing_database(tmp_path):
    with pytest.raises(FileNotFoundError, match="competence init"):
        prepare_database(tmp_path / "nope.sqlite", tmp_path / "backups")
    assert not (tmp_path / "nope.sqlite").exists()


def test_prepare_backs_up_then_migrates(db_path, tmp_path):
    taken = prepare_database(db_path, tmp_path / "backups")
    assert taken.exists()
