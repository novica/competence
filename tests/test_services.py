import pytest

from competence.core import services


@pytest.fixture
def started(conn):
    """A round, a person and a started assessment as a Data Scientist."""
    rnd = services.create_round(conn, "2026-Q4", "2026-12-01")
    person = services.create_person(conn, "Test Person")
    role = next(r for r in services.list_active_roles(conn) if r.name == "Data Scientist")
    return services.start_assessment(conn, rnd.id, person.id, role.id)


def skill_id(conn, name):
    return conn.execute("SELECT id FROM skills WHERE name = ?", (name,)).fetchone()[0]


def test_round_and_person_validation(conn):
    services.create_round(conn, "R1", None)
    with pytest.raises(services.Invalid, match="already exists"):
        services.create_round(conn, " R1 ", None)
    with pytest.raises(services.Invalid, match="required"):
        services.create_round(conn, "  ", None)
    with pytest.raises(services.Invalid, match="date"):
        services.create_round(conn, "R2", "2026-13-45")
    services.create_person(conn, "Alex")
    with pytest.raises(services.Invalid, match="already"):
        services.create_person(conn, "Alex")


def test_rounds_listed_newest_first(conn):
    services.create_round(conn, "old", "2026-01-01")
    services.create_round(conn, "new", "2026-06-01")
    services.create_round(conn, "undated", None)
    assert [r.label for r in services.list_rounds(conn)] == ["undated", "new", "old"]


def test_start_assessment_rules(conn, started):
    with pytest.raises(services.Invalid, match="already"):
        services.start_assessment(conn, started.round.id, started.person.id, started.role.id)
    other = services.create_person(conn, "Other")
    with pytest.raises(services.Invalid, match="role"):
        services.start_assessment(conn, started.round.id, other.id, 9999)
    with pytest.raises(services.NotFound):
        services.start_assessment(conn, 9999, other.id, started.role.id)


def test_start_assessment_twice_at_once(conn, started, monkeypatch):
    """A double click can pass the 'already started' check twice; the UNIQUE constraint still gives Invalid."""
    monkeypatch.setattr(services, "get_assessment", lambda *a: None)  # the check sees nothing yet
    with pytest.raises(services.Invalid, match="already"):
        services.start_assessment(conn, started.round.id, started.person.id, started.role.id)


def test_rating_rows_cover_active_skills_with_targets(conn, started):
    rows = services.rating_rows(conn, started.id)
    assert len(rows) == 132
    assert all(r.target is not None for r in rows)
    assert all(r.self_level is None for r in rows)
    assert {r.tag for r in services.rating_rows(conn, started.id, "Python")} == {"Python"}
    assert len(services.rating_rows(conn, started.id, "Python")) == 16
    assert services.rating_rows(conn, started.id, "Julia") == []


def test_save_rating_upserts_and_computes_gap(conn, started):
    sid = skill_id(conn, "ggplot2")  # Data Scientist target 5
    row = services.save_rating(conn, started.id, sid, 3, None, 2)
    assert (row.self_level, row.agreed, row.desire, row.used, row.target, row.gap) == (3, None, 2, 3, 5, -2)
    row = services.save_rating(conn, started.id, sid, 3, 5, 2)
    assert (row.used, row.gap) == (5, 0)
    assert conn.execute("SELECT COUNT(*) FROM ratings").fetchone()[0] == 1


def test_save_rating_all_blank_removes_row(conn, started):
    sid = skill_id(conn, "ggplot2")
    services.save_rating(conn, started.id, sid, 3, None, None)
    row = services.save_rating(conn, started.id, sid, None, None, None)
    assert row.self_level is None
    assert conn.execute("SELECT COUNT(*) FROM ratings").fetchone()[0] == 0


@pytest.mark.parametrize("bad", [4, -1, 13, 10])
def test_save_rating_rejects_non_fibonacci(conn, started, bad):
    with pytest.raises(services.Invalid, match="0, 1, 2, 3, 5, 8"):
        services.save_rating(conn, started.id, skill_id(conn, "ggplot2"), bad, None, None)
    assert conn.execute("SELECT COUNT(*) FROM ratings").fetchone()[0] == 0


def test_retired_skills_are_hidden_and_cannot_be_rated(conn, started):
    sid = skill_id(conn, "Shiny")
    conn.execute("UPDATE skills SET retired_on = '2026-01-01' WHERE id = ?", (sid,))
    conn.commit()
    assert sid not in {r.skill_id for r in services.rating_rows(conn, started.id)}
    with pytest.raises(services.NotFound):
        services.save_rating(conn, started.id, sid, 3, None, None)


def test_round_status_counts_and_last_role(conn, started):
    services.save_rating(conn, started.id, skill_id(conn, "ggplot2"), 3, None, None)
    services.save_rating(conn, started.id, skill_id(conn, "SQL"), None, None, 2)
    nxt = services.create_round(conn, "2027-Q1", "2027-03-01")
    status = services.round_status(conn, started.round.id)
    assert [(s.person.name, s.rated, s.role_name) for s in status] == [("Test Person", 2, "Data Scientist")]
    later = services.round_status(conn, nxt.id)[0]
    assert later.assessment_id is None and later.last_role_id == started.role.id


@pytest.mark.parametrize("bad", [0, 4, 5, 8, -1])
def test_save_rating_rejects_desire_outside_1_to_3(conn, started, bad):
    with pytest.raises(services.Invalid, match="desire must be one of 1, 2, 3"):
        services.save_rating(conn, started.id, skill_id(conn, "ggplot2"), 3, None, bad)
