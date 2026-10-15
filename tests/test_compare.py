"""Compare two rounds (team and person), trends, skills added or retired in between."""

import pytest
from fastapi.testclient import TestClient

from competence import seed
from competence.core import services
from competence.core.db import connect
from competence.web.app import create_app


def sid(conn, name):
    return conn.execute("SELECT id FROM skills WHERE name = ?", (name,)).fetchone()[0]


@pytest.fixture
def rounds(conn):
    """Three rounds. Between R1 and R2: Shiny retired, a brand-new skill added and first rated in R2."""
    r1 = services.create_round(conn, "R1", "2026-03-01")
    r2 = services.create_round(conn, "R2", "2026-06-01")
    r3 = services.create_round(conn, "R3", "2026-09-01")
    roles = {r.name: r for r in services.list_active_roles(conn)}
    ann, bo = services.create_person(conn, "Ann"), services.create_person(conn, "Bo")
    g, sql, shiny = sid(conn, "ggplot2"), sid(conn, "SQL"), sid(conn, "Shiny")
    a1 = services.start_assessment(conn, r1.id, ann.id, roles["Data Scientist"].id)
    b1 = services.start_assessment(conn, r1.id, bo.id, roles["Data Analyst"].id)
    services.save_rating(conn, a1.id, g, 2, None, 2)
    services.save_rating(conn, b1.id, g, 3, None, 1)
    services.save_rating(conn, a1.id, sql, 5, None, None)
    services.save_rating(conn, a1.id, shiny, 3, None, None)
    with conn:
        conn.execute("UPDATE skills SET retired_on = '2026-04-01' WHERE id = ?", (shiny,))
        new = conn.execute(
            "INSERT INTO skills (category, name, tag, priority, experts_needed, sort_order)"
            " VALUES ('Python Programming', 'Brand-new', 'Python', 2, 1, 999)"
        ).lastrowid
        conn.execute("INSERT INTO targets (role_id, skill_id, level) SELECT id, ?, 3 FROM roles", (new,))
    a2 = services.start_assessment(conn, r2.id, ann.id, roles["Data Scientist"].id)  # pre-filled
    services.start_assessment(conn, r2.id, bo.id, roles["Data Analyst"].id)
    services.save_rating(conn, a2.id, g, 5, None, 2)  # 2 -> 5
    services.save_rating(conn, a2.id, sql, 3, None, None)  # 5 -> 3
    services.save_rating(conn, a2.id, new, 2, None, 2)
    a3 = services.start_assessment(conn, r3.id, ann.id, roles["Statistician"].id)  # role change
    services.save_rating(conn, a3.id, g, 8, None, 2)
    return {"r1": r1, "r2": r2, "r3": r3, "ann": ann, "bo": bo, "g": g, "sql": sql, "new": new}


def row(cmp, skill_id):
    return next(r for r in cmp.rows if r.skill_id == skill_id)


def test_team_comparison(conn, rounds):
    cmp = services.compare_team(conn, rounds["r1"].id, rounds["r2"].id)
    g = row(cmp, rounds["g"])
    assert (g.before.median_level, g.after.median_level, g.level_delta) == (2, 3, 1)  # lower medians; Bo stays at 3
    assert [r.name for r in cmp.improvements][:1] == ["ggplot2"]
    assert [r.name for r in cmp.declines] == ["SQL"]
    assert [r.name for r in cmp.first_rated] == ["Brand-new"]
    assert cmp.retired_between == [("Shiny", "2026-04-01")]
    assert rounds["new"] in {r.skill_id for r in cmp.rows}
    assert "Shiny" not in {r.name for r in cmp.rows}
    assert (cmp.before.people_assessed, cmp.after.people_assessed) == (2, 2)


def test_trend_runs_over_all_rounds(conn, rounds):
    cmp = services.compare_team(conn, rounds["r1"].id, rounds["r3"].id)
    assert [r.label for r in cmp.rounds] == ["R1", "R2", "R3"]
    assert row(cmp, rounds["g"]).trend == [2, 3, 8]
    assert row(cmp, rounds["new"]).trend == [None, 2, 2]  # pre-filled into R3, nobody before R2


def test_reverse_order_and_same_round(conn, rounds):
    back = services.compare_team(conn, rounds["r2"].id, rounds["r1"].id)
    assert row(back, rounds["g"]).level_delta == -1
    with pytest.raises(services.Invalid, match="different"):
        services.compare_team(conn, rounds["r1"].id, rounds["r1"].id)
    with pytest.raises(services.NotFound):
        services.compare_team(conn, rounds["r1"].id, 999)


def test_retired_between_needs_dates(conn, rounds):
    undated = services.create_round(conn, "Undated", None)
    assert services.compare_team(conn, rounds["r1"].id, undated.id).retired_between == []


def test_person_comparison(conn, rounds):
    p = services.compare_person(conn, rounds["r1"].id, rounds["r3"].id, rounds["ann"].id)
    assert (p.before.role.name, p.after.role.name) == ("Data Scientist", "Statistician")
    g = next(r for r in p.rows if r.skill_id == rounds["g"])
    assert (g.before_used, g.after_used, g.level_delta, g.trend) == (2, 8, 6, [2, 5, 8])
    stat_target = conn.execute(
        "SELECT t.level FROM targets t JOIN roles r ON r.id = t.role_id"
        " WHERE r.name = 'Statistician' AND t.skill_id = ?",
        (rounds["g"],),
    ).fetchone()[0]
    assert g.target == stat_target  # later round's role
    assert p.improvements[0].name == "ggplot2"
    assert [r.name for r in p.declines] == ["SQL"]
    nobody = services.compare_person(conn, rounds["r2"].id, rounds["r3"].id, rounds["bo"].id)
    assert nobody.after is None and nobody.improvements == []


def test_tag_filter(conn, rounds):
    cmp = services.compare_team(conn, rounds["r1"].id, rounds["r2"].id, "Python")
    assert {r.tag for r in cmp.rows} == {"Python"} and len(cmp.rows) == 17
    assert cmp.retired_between == []  # Shiny is R


# ---------- page ----------


@pytest.fixture
def client(db_path):
    with TestClient(create_app(db_path), base_url="http://localhost") as c:
        yield c


def test_compare_page_defaults_to_latest_two(client, rounds):
    page = client.get("/compare").text
    assert "Team, R2 → R3" in page and "Biggest improvements" in page
    page = client.get(f"/compare?from={rounds['r1'].id}&to={rounds['r2'].id}").text
    assert "Rated in R2 but not in R1" in page and "Brand-new" in page and "Retired in between" in page
    assert "2 → 3" in page and "why medians" in page  # ggplot2 medians


def test_compare_page_person_and_errors(client, rounds):
    page = client.get(f"/compare?from={rounds['r1'].id}&to={rounds['r3'].id}&person={rounds['ann'].id}").text
    assert "Ann, R1 (Data Scientist)" in page and "2 → 5 → 8" in page
    r = client.get(f"/compare?from={rounds['r1'].id}&to={rounds['r1'].id}")
    assert r.status_code == 400 and "two different rounds" in r.text
    assert client.get(f"/compare?from={rounds['r1'].id}&to=999").status_code == 404
    page = client.get(f"/compare?from={rounds['r1'].id}&to={rounds['r2'].id}&changed=true").text
    assert "ggplot2" in page and "NumPy" not in page


def test_compare_page_accepts_blank_form_fields(client, rounds):
    """The GET form sends "Whole team" and "Everyone" as blanks."""
    r1, r2 = rounds["r1"].id, rounds["r2"].id
    assert "Team, R1 → R2" in client.get(f"/compare?from={r1}&to={r2}&person=&team=&tag=").text
    page = client.get(f"/compare?from={r1}&to={r2}&person={rounds['ann'].id}&team=&tag=&changed=true").text
    assert "Ann, R1" in page
    assert client.get(f"/compare?from={r1}&to={r2}&team=abc").status_code == 422


def test_compare_page_needs_two_rounds(client, conn):
    services.create_round(conn, "Only", None)
    assert "at least two rounds" in client.get("/compare").text


def test_demo_compare(tmp_path):
    path = tmp_path / "demo.sqlite"
    seed.create_database(path, demo=True)
    c = connect(path)
    q = {r["label"]: r["id"] for r in c.execute("SELECT id, label FROM rounds")}
    cmp = services.compare_team(c, q["2026-Q1"], q["2026-Q3"])
    c.close()
    assert cmp.retired_between == [seed.DEMO_RETIRED]
    assert seed.DEMO_ADDED_IN_Q2 in {r.name for r in cmp.first_rated}
    assert all(len(r.trend) == 3 for r in cmp.rows)


def test_changed_means_someone_in_both_rounds_changed(conn, rounds):
    """Opposite moves that cancel out still count; a newcomer moving the median doesn't."""
    roles = {r.name: r for r in services.list_active_roles(conn)}
    r4, r5 = services.create_round(conn, "R4", "2026-10-01"), services.create_round(conn, "R5", "2026-11-01")
    cy = services.create_person(conn, "Cy")
    sql, py = rounds["sql"], sid(conn, "pandas / polars")
    a4 = services.start_assessment(conn, r4.id, rounds["ann"].id, roles["Data Scientist"].id)
    b4 = services.start_assessment(conn, r4.id, rounds["bo"].id, roles["Data Analyst"].id)
    for a, level in ((a4, 3), (b4, 5)):
        services.save_rating(conn, a.id, sql, level, None, 1)
        services.save_rating(conn, a.id, py, level, None, 1)
    a5 = services.start_assessment(conn, r5.id, rounds["ann"].id, roles["Data Scientist"].id)  # pre-filled
    b5 = services.start_assessment(conn, r5.id, rounds["bo"].id, roles["Data Analyst"].id)
    services.save_rating(conn, a5.id, sql, 5, None, 1)  # 3 -> 5
    services.save_rating(conn, b5.id, sql, 3, None, 1)  # 5 -> 3: the median stays 3
    c5 = services.start_assessment(conn, r5.id, cy.id, roles["Data Analyst"].id)
    services.save_rating(conn, c5.id, py, 8, None, 2)  # only Cy moves pandas' median (3 -> 5)
    cmp = services.compare_team(conn, r4.id, r5.id)
    assert row(cmp, sql).level_delta == 0 and row(cmp, sql).changed
    assert row(cmp, py).level_delta != 0 and not row(cmp, py).changed
