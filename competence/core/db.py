"""SQLite connection and schema migrations."""

import re
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


class MigrationError(RuntimeError):
    pass


def connect(path: str | Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open the database with foreign keys enforced and rows addressable by column name.

    The web layer passes check_same_thread=False: it opens one connection per request, and FastAPI
    may run the dependency and the route in different worker threads. The connection is never shared.
    """
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def available_migrations(directory: Path = MIGRATIONS_DIR) -> list[tuple[int, Path]]:
    """Migration files as (version, path), checked to be numbered 1, 2, 3, ... with no gaps."""
    found = []
    for path in directory.glob("*.sql"):
        m = _MIGRATION_NAME.match(path.name)
        if not m:
            raise MigrationError(f"badly named migration file: {path.name} (expected NNN_name.sql)")
        found.append((int(m.group(1)), path))
    found.sort()
    versions = [v for v, _ in found]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f"migration numbers must run 1, 2, 3, ... without gaps; found {versions}")
    return found


def schema_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL) STRICT")
    conn.commit()
    return conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]


def migrate(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> list[int]:
    """Apply pending migrations in order, each in its own transaction. Returns the versions applied.

    Refuses to touch a database that is newer than this code, so an old checkout can't damage it.
    Foreign keys are off while a migration runs, so it can rebuild a table that others point at
    (SQLite's documented procedure); every reference is checked before the migration commits.
    """
    migrations = available_migrations(directory)
    current = schema_version(conn)
    latest = migrations[-1][0] if migrations else 0
    if current > latest:
        raise MigrationError(f"database is at schema version {current}, but this code only knows up to {latest}")
    applied = []
    for version, path in migrations:
        if version <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        fk_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.execute("PRAGMA foreign_keys = OFF")  # a no-op inside a transaction, so set it first
        try:
            conn.executescript(f"BEGIN;\n{sql}\nINSERT INTO schema_version (version) VALUES ({version});")
            broken = conn.execute("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise MigrationError(
                    f"migration {path.name} failed: it leaves {len(broken)} broken references"
                    f" (first in table {broken[0][0]})"
                )
            conn.commit()
        except (sqlite3.Error, MigrationError) as e:
            conn.rollback()
            if isinstance(e, MigrationError):
                raise
            raise MigrationError(f"migration {path.name} failed: {e}") from e
        finally:
            conn.execute(f"PRAGMA foreign_keys = {'ON' if fk_on else 'OFF'}")
        applied.append(version)
    return applied
