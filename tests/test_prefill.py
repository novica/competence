"""A new assessment starts from the person's latest earlier one."""

import pytest

from competence.core import services


def sid(conn, name):
    return conn.execute("SELECT id FROM skills WHERE name = ?", (name,)).fetchone()[0]


def role(conn, name):
    return next(r for r in services.list_active_roles(conn) if r.name == name)


def ratings(conn, a):
    return {
        r["name"]: (r["self"], r["agreed"], r["desire"])
        for r in conn.execute(
            "SELECT s.name, x.self, x.agreed, x.desire FROM ratings x JOIN skills s ON s.id = x.skill_id"
            " WHERE x.assessment_id = ?",
            (a.id,),
        )
    }


def add_skill(conn, name):
    """Stand-in for Admin → Add skill: a new skill with a target for every role."""
    with conn:
        new = conn.execute(
            "INSERT INTO skills (category, name, tag, priority, experts_needed, sort_order)"
            " VALUES ('Python Programming', ?, 'Python', 2, 1, 999)",
            (name,),
        ).lastrowid
        conn.execute("INSERT INTO targets (role_id, skill_id, level) SELECT id, ?, 3 FROM roles", (new,))
    return new


@pytest.fixture
def history(conn):
    """Round 1 rated, then between rounds Shiny is retired and a new skill is added."""
    r1 = services.create_round(conn, "R1", "2026-03-01")
    r2 = services.create_round(conn, "R2", "2026-06-01")
    p = services.create_person(conn, "Pat")
    a1 = services.start_assessment(conn, r1.id, p.id, role(conn, "Data Scientist").id)
    services.save_rating(conn, a1.id, sid(conn, "ggplot2"), 3, 5, 2)  # agreed overrides self
    services.save_rating(conn, a1.id, sid(conn, "SQL"), 2, None, None)
    services.save_rating(conn, a1.id, sid(conn, "Shiny"), 1, None, 1)
    services.save_rating(conn, a1.id, sid(conn, "Core Python"), None, None, 2)  # desire only
    with conn:
        conn.execute("UPDATE skills SET retired_on = '2026-04-01' WHERE name = 'Shiny'")
    new = add_skill(conn, "Brand-new skill")
    return {"r1": r1, "r2": r2, "p": p, "a1": a1, "new": new}


def test_prefill_copies_level_used_and_desire(conn, history):
    a2 = services.start_assessment(conn, history["r2"].id, history["p"].id, role(conn, "Data Scientist").id)
    assert ratings(conn, a2) == {
        "ggplot2": (5, None, 2),  # self = last level used (the agreed 5), agreed blank, desire kept
        "SQL": (2, None, None),
        "Core Python": (None, None, 2),  # nothing to use as level; desire still carries over
    }


def test_retired_skill_is_not_copied_and_not_shown(conn, history):
    a2 = services.start_assessment(conn, history["r2"].id, history["p"].id, role(conn, "Data Scientist").id)
    assert "Shiny" not in ratings(conn, a2)
    assert "Shiny" not in {r.name for r in services.rating_rows(conn, a2.id)}
    assert ratings(conn, history["a1"])["Shiny"] == (1, None, 1)  # history kept


def test_new_skill_is_blank_and_marked_as_compared(conn, history):
    a2 = services.start_assessment(conn, history["r2"].id, history["p"].id, role(conn, "Data Scientist").id)
    row = next(r for r in services.rating_rows(conn, a2.id) if r.skill_id == history["new"])
    assert (row.self_level, row.agreed, row.desire, row.prev_used, row.prev_desire) == (None,) * 5
    assert row.compared and not (row.self_level_changed or row.agreed_changed or row.desire_changed)


def test_prefilled_copy_is_independent_of_last_round(conn, history):
    a2 = services.start_assessment(conn, history["r2"].id, history["p"].id, role(conn, "Data Scientist").id)
    services.save_rating(conn, a2.id, sid(conn, "ggplot2"), 8, None, 2)
    assert ratings(conn, history["a1"])["ggplot2"] == (3, 5, 2)


def test_changed_flags(conn, history):
    a2 = services.start_assessment(conn, history["r2"].id, history["p"].id, role(conn, "Data Scientist").id)
    g = sid(conn, "ggplot2")
    row = next(r for r in services.rating_rows(conn, a2.id) if r.skill_id == g)
    assert (row.prev_used, row.prev_desire) == (5, 2)
    assert not (row.self_level_changed or row.agreed_changed or row.desire_changed)
    row = services.save_rating(conn, a2.id, g, 8, None, 2)
    assert row.self_level_changed and not row.desire_changed
    row = services.save_rating(conn, a2.id, g, 8, 5, 1)  # agreed back to last level: not a change
    assert not row.agreed_changed and row.desire_changed
    row = services.save_rating(conn, a2.id, g, 8, 3, 2)
    assert row.agreed_changed


def test_first_assessment_has_nothing_to_compare(conn, history):
    rows = services.rating_rows(conn, history["a1"].id)
    assert not any(r.compared for r in rows)
    assert not any(r.self_level_changed or r.agreed_changed or r.desire_changed for r in rows)
    assert services.previous_assessment(conn, history["a1"]) is None


def test_role_change_still_prefills(conn, history):
    a2 = services.start_assessment(conn, history["r2"].id, history["p"].id, role(conn, "Statistician").id)
    assert ratings(conn, a2)["ggplot2"] == (5, None, 2)
    prev = services.previous_assessment(conn, a2)
    assert prev.round.label == "R1" and prev.role.name == "Data Scientist"


def test_uses_latest_earlier_round_not_later_ones(conn, history):
    p, ds = history["p"], role(conn, "Data Scientist").id
    a2 = services.start_assessment(conn, history["r2"].id, p.id, ds)
    services.save_rating(conn, a2.id, sid(conn, "SQL"), 5, None, None)
    between = services.create_round(conn, "R1.5", "2026-04-15")  # created later, dated in between
    a_mid = services.start_assessment(conn, between.id, p.id, ds)
    assert ratings(conn, a_mid)["SQL"] == (2, None, None)  # from R1, not the later R2
    before = services.create_round(conn, "R0", "2026-01-01")
    a0 = services.start_assessment(conn, before.id, p.id, ds)
    assert ratings(conn, a0) == {}  # nothing earlier than R0
    undated = services.create_round(conn, "Next", None)  # undated counts as newest
    a_next = services.start_assessment(conn, undated.id, p.id, ds)
    assert ratings(conn, a_next)["SQL"] == (5, None, None)


def test_other_people_are_not_mixed_in(conn, history):
    other = services.create_person(conn, "Other")
    a = services.start_assessment(conn, history["r2"].id, other.id, role(conn, "Data Analyst").id)
    assert ratings(conn, a) == {}


def test_round_status_shows_where_prefill_comes_from(conn, history):
    status = {s.person.name: s for s in services.round_status(conn, history["r2"].id)}
    assert status["Pat"].last_round_label == "R1"
    assert status["Pat"].last_role_id == role(conn, "Data Scientist").id
    first = {s.person.name: s for s in services.round_status(conn, history["r1"].id)}
    assert first["Pat"].last_round_label is None
