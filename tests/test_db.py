import sqlite3

import pytest

from competence.core.db import MigrationError, available_migrations, connect, migrate, schema_version


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.sqlite")
    yield c
    c.close()


def test_migrate_applies_all_then_nothing(conn):
    latest = available_migrations()[-1][0]
    assert migrate(conn) == list(range(1, latest + 1))
    assert schema_version(conn) == latest
    assert migrate(conn) == []


def test_refuses_database_newer_than_code(conn):
    migrate(conn)
    conn.execute("INSERT INTO schema_version (version) VALUES (999)")
    conn.commit()
    with pytest.raises(MigrationError, match="999"):
        migrate(conn)


def test_failed_migration_rolls_back(tmp_path, conn):
    d = tmp_path / "migs"
    d.mkdir()
    (d / "001_ok.sql").write_text("CREATE TABLE a (x INTEGER) STRICT;")
    (d / "002_bad.sql").write_text("CREATE TABLE b (x INTEGER) STRICT; INSERT INTO nope VALUES (1);")
    with pytest.raises(MigrationError, match="002_bad"):
        migrate(conn, d)
    assert schema_version(conn) == 1
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "a" in tables and "b" not in tables


def test_migration_leaving_broken_references_rolls_back(tmp_path, conn):
    d = tmp_path / "migs"
    d.mkdir()
    (d / "001_ok.sql").write_text(
        "CREATE TABLE p (id INTEGER PRIMARY KEY) STRICT;"
        " CREATE TABLE c (p_id INTEGER REFERENCES p (id)) STRICT;"
        " INSERT INTO p VALUES (1); INSERT INTO c VALUES (1);"
    )
    (d / "002_bad.sql").write_text("DELETE FROM p;")
    with pytest.raises(MigrationError, match="002_bad.*broken references"):
        migrate(conn, d)
    assert schema_version(conn) == 1
    assert conn.execute("SELECT COUNT(*) FROM p").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1  # switched back on afterwards


def test_skill_tags_migration_keeps_ids_and_references(tmp_path, conn):
    """002 rebuilds skills: tags carry over, every rating and target still points at its skill."""
    d = tmp_path / "migs"
    d.mkdir()
    first, second = [path for _, path in available_migrations()[:2]]
    (d / first.name).write_text(first.read_text(encoding="utf-8"), encoding="utf-8")
    migrate(conn, d)
    _role, skill, _aid = _base_rows_v1(conn)
    (d / second.name).write_text(second.read_text(encoding="utf-8"), encoding="utf-8")
    assert migrate(conn, d) == [2]
    assert [tuple(r) for r in conn.execute("SELECT id, tag FROM skills")] == [(skill, "R")]
    assert conn.execute("SELECT COUNT(*) FROM ratings r JOIN skills s ON s.id = r.skill_id").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM targets t JOIN skills s ON s.id = t.skill_id").fetchone()[0] == 1
    conn.execute("INSERT INTO skills (category, name, priority, experts_needed, sort_order) VALUES ('C', 'X', 2, 1, 2)")
    assert conn.execute("SELECT tag FROM skills WHERE name = 'X'").fetchone()[0] == ""  # blank = untagged
    with pytest.raises(sqlite3.IntegrityError):  # still enforced
        conn.execute("DELETE FROM skills WHERE id = ?", (skill,))


def _base_rows_v1(conn):
    """A skill (ecosystem R) with a target and a rating, in a database at schema version 1."""
    role = conn.execute("INSERT INTO roles (name, sort_order) VALUES ('Analyst', 1)").lastrowid
    skill = conn.execute(
        "INSERT INTO skills (category, name, ecosystem, priority, experts_needed, sort_order)"
        " VALUES ('R Programming', 'ggplot2', 'R', 2, 1, 1)"
    ).lastrowid
    conn.execute("INSERT INTO targets VALUES (?, ?, 3)", (role, skill))
    person = conn.execute("INSERT INTO people (name) VALUES ('P')").lastrowid
    rnd = conn.execute("INSERT INTO rounds (label) VALUES ('Q1')").lastrowid
    aid = conn.execute(
        "INSERT INTO assessments (person_id, round_id, role_id) VALUES (?, ?, ?)", (person, rnd, role)
    ).lastrowid
    conn.execute("INSERT INTO ratings (assessment_id, skill_id, self) VALUES (?, ?, 3)", (aid, skill))
    conn.commit()
    return role, skill, aid


def test_migration_numbering_must_have_no_gaps(tmp_path):
    (tmp_path / "001_a.sql").write_text("")
    (tmp_path / "003_c.sql").write_text("")
    with pytest.raises(MigrationError, match="gaps"):
        available_migrations(tmp_path)


def _base_rows(conn):
    migrate(conn)
    role = conn.execute("INSERT INTO roles (name, sort_order) VALUES ('R', 0)").lastrowid
    skill = conn.execute(
        "INSERT INTO skills (category, name, tag, priority, experts_needed, sort_order)"
        " VALUES ('C', 'S', 'General', 2, 1, 0)"
    ).lastrowid
    person = conn.execute("INSERT INTO people (name) VALUES ('P')").lastrowid
    rnd = conn.execute("INSERT INTO rounds (label) VALUES ('2026-Q1')").lastrowid
    aid = conn.execute(
        "INSERT INTO assessments (person_id, round_id, role_id) VALUES (?, ?, ?)", (person, rnd, role)
    ).lastrowid
    return role, skill, aid


@pytest.mark.parametrize("value", [4, 6, 7, -1, 9])
def test_ratings_reject_non_fibonacci_levels(conn, value):
    _, skill, aid = _base_rows(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO ratings (assessment_id, skill_id, self) VALUES (?, ?, ?)", (aid, skill, value))


def test_ratings_accept_fibonacci_and_null(conn):
    _, skill, aid = _base_rows(conn)
    conn.execute(
        "INSERT INTO ratings (assessment_id, skill_id, self, agreed, desire) VALUES (?, ?, 5, NULL, 2)", (aid, skill)
    )


def test_constraints_on_skills_targets_and_foreign_keys(conn):
    role, skill, aid = _base_rows(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO skills (category, name, tag, priority, experts_needed, sort_order)"
            " VALUES ('C', 'Y', 'R', 4, 1, 1)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO targets (role_id, skill_id, level) VALUES (?, ?, 4)", (role, skill))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO ratings (assessment_id, skill_id, self) VALUES (?, 999, 1)", (aid,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE skills SET retired_on = 'next week' WHERE id = ?", (skill,))


def test_desire_migration_maps_to_three_points(tmp_path, conn):
    """007 maps desire 0 -> 0, 1-3 -> 1, 5-8 -> 2, keeps skill levels, and sets the article's desire texts."""
    d = tmp_path / "migs"
    d.mkdir()
    migrations = [path for _, path in available_migrations()]
    for path in migrations[:6]:
        (d / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    migrate(conn, d)
    role = conn.execute("INSERT INTO roles (name, sort_order) VALUES ('Analyst', 1)").lastrowid
    person = conn.execute("INSERT INTO people (name) VALUES ('P')").lastrowid
    rnd = conn.execute("INSERT INTO rounds (label) VALUES ('Q1')").lastrowid
    aid = conn.execute(
        "INSERT INTO assessments (person_id, round_id, role_id) VALUES (?, ?, ?)", (person, rnd, role)
    ).lastrowid
    old = [0, 1, 2, 3, 5, 8, None]
    for i, desire in enumerate(old):
        sid = conn.execute(
            "INSERT INTO skills (category, name, priority, experts_needed, sort_order) VALUES ('C', ?, 2, 1, ?)",
            (f"s{i}", i),
        ).lastrowid
        conn.execute(
            "INSERT INTO ratings (assessment_id, skill_id, self, desire) VALUES (?, ?, 8, ?)", (aid, sid, desire)
        )
    conn.commit()
    (d / migrations[6].name).write_text(migrations[6].read_text(encoding="utf-8"), encoding="utf-8")
    assert migrate(conn, d) == [7]
    got = [
        tuple(r)
        for r in conn.execute(
            "SELECT x.self, x.desire FROM ratings x JOIN skills s ON s.id = x.skill_id ORDER BY s.sort_order"
        )
    ]
    assert got == [(8, 0), (8, 1), (8, 1), (8, 1), (8, 2), (8, 2), (8, None)]
    texts = conn.execute("SELECT level, label FROM level_labels WHERE scale = 'desire' ORDER BY level")
    assert [tuple(r) for r in texts] == [
        (0, "Does not interest me"),
        (1, "Happy to perform this work"),
        (2, "Passionate to continue learning"),
    ]
    assert conn.execute("SELECT COUNT(*) FROM level_labels WHERE scale = 'level'").fetchone()[0] == 6
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE ratings SET desire = 5")


def test_desire_migration_moves_to_one_to_three(tmp_path, conn):
    """008 adds one to every desire, and the desire texts move with their points (edited texts too)."""
    d = tmp_path / "migs"
    d.mkdir()
    migrations = [path for _, path in available_migrations()]
    for path in migrations[:7]:
        (d / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    migrate(conn, d)
    role = conn.execute("INSERT INTO roles (name, sort_order) VALUES ('Analyst', 1)").lastrowid
    person = conn.execute("INSERT INTO people (name) VALUES ('P')").lastrowid
    rnd = conn.execute("INSERT INTO rounds (label) VALUES ('Q1')").lastrowid
    aid = conn.execute(
        "INSERT INTO assessments (person_id, round_id, role_id) VALUES (?, ?, ?)", (person, rnd, role)
    ).lastrowid
    for i, desire in enumerate([0, 1, 2, None]):
        sid = conn.execute(
            "INSERT INTO skills (category, name, priority, experts_needed, sort_order) VALUES ('C', ?, 2, 1, ?)",
            (f"s{i}", i),
        ).lastrowid
        conn.execute(
            "INSERT INTO ratings (assessment_id, skill_id, self, desire) VALUES (?, ?, 3, ?)", (aid, sid, desire)
        )
    conn.execute("UPDATE level_labels SET label = 'Rather not' WHERE scale = 'desire' AND level = 0")
    conn.commit()
    (d / migrations[7].name).write_text(migrations[7].read_text(encoding="utf-8"), encoding="utf-8")
    assert migrate(conn, d) == [8]
    got = [
        tuple(r)
        for r in conn.execute(
            "SELECT x.self, x.desire FROM ratings x JOIN skills s ON s.id = x.skill_id ORDER BY s.sort_order"
        )
    ]
    assert got == [(3, 1), (3, 2), (3, 3), (3, None)]
    texts = conn.execute("SELECT level, label FROM level_labels WHERE scale = 'desire' ORDER BY level")
    assert [tuple(r) for r in texts] == [
        (1, "Rather not"),
        (2, "Happy to perform this work"),
        (3, "Passionate to continue learning"),
    ]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE ratings SET desire = 0")
