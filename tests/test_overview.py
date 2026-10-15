"""Team overview and person profile on a small, hand-checked team."""

import re

import pytest
from fastapi.testclient import TestClient

from competence.core import metrics, services
from competence.core.db import connect
from competence.web.app import create_app

SAME = {"Origin": "http://localhost"}


def sid(conn, name):
    return conn.execute("SELECT id FROM skills WHERE name = ?", (name,)).fetchone()[0]


def role(conn, name):
    return next(r for r in services.list_active_roles(conn) if r.name == name)


@pytest.fixture
def team(conn):
    """Two rounds. In R2 two people rated ggplot2 (priority and targets from the catalogue)."""
    r1 = services.create_round(conn, "R1", "2026-03-01")
    r2 = services.create_round(conn, "R2", "2026-06-01")
    ann = services.create_person(conn, "Ann")
    bo = services.create_person(conn, "Bo")
    ds, da = role(conn, "Data Scientist"), role(conn, "Data Analyst")
    g, sql = sid(conn, "ggplot2"), sid(conn, "SQL")
    a1 = services.start_assessment(conn, r1.id, ann.id, ds.id)
    services.save_rating(conn, a1.id, g, 2, None, 3)
    a2 = services.start_assessment(conn, r2.id, ann.id, ds.id)  # pre-filled: ggplot2 self 2, desire 3
    services.save_rating(conn, a2.id, g, 3, 5, 3)  # peer review: agreed 5
    b2 = services.start_assessment(conn, r2.id, bo.id, da.id)
    services.save_rating(conn, b2.id, g, 2, None, 3)
    services.save_rating(conn, b2.id, sql, 3, None, 2)
    return {"r1": r1, "r2": r2, "ann": ann, "bo": bo, "g": g, "sql": sql}


def row(ov, skill_id):
    return next(r for r in ov.skills if r.skill_id == skill_id)


def test_team_overview_per_skill(conn, team):
    ov = services.team_overview(conn, team["r2"].id)
    assert len(ov.skills) == 132 and ov.previous_round.label == "R1"
    s = row(ov, team["g"]).stats
    # ggplot2 targets: Data Scientist 5, Data Analyst 5. Ann used 5 (agreed), Bo 2.
    assert (s.assessed, s.median_level, s.max_level, s.n3, s.n5) == (2, 2, 5, 1, 1)
    assert (s.below, s.shortfall, s.dev_candidates, s.median_desire) == (1, 3, 1, 3)  # Bo: level 2, desire 3
    prio = conn.execute("SELECT priority FROM skills WHERE id = ?", (team["g"],)).fetchone()[0]
    assert s.weighted_shortfall == 3 * prio
    assert s.median_competency == 6  # 6 and 15: the lower one
    assert row(ov, sid(conn, "NumPy")).stats.assessed == 0 and row(ov, sid(conn, "NumPy")).stats.flag is None


def test_headline_and_change_since_previous_round(conn, team):
    ov = services.team_overview(conn, team["r2"].id)
    h, hp = ov.headline, ov.previous_headline
    assert h.people_assessed == 2 and hp.people_assessed == 1
    assert h.delta(hp, "people_assessed") == 1
    # R2 pairs with a target: Ann ggplot2 5/5 ok, Bo ggplot2 2/5 below, Bo SQL 3 vs DA target 5 below
    assert h.pct_at_target == pytest.approx(100 / 3)
    assert hp.pct_at_target == 0  # R1: Ann 2 vs 5
    first = services.team_overview(conn, team["r1"].id)
    assert first.previous_round is None and first.previous_headline is None


def test_tag_filter_and_retired_skills(conn, team):
    assert len(services.team_overview(conn, team["r2"].id, "Python").skills) == 16
    with conn:
        conn.execute("UPDATE skills SET retired_on = '2026-07-01' WHERE id = ?", (team["sql"],))
    ov = services.team_overview(conn, team["r2"].id)
    assert team["sql"] not in {r.skill_id for r in ov.skills}
    assert len(ov.skills) == 131
    assert services.team_overview(conn, team["r2"].id, "Julia").skills == []  # a tag nobody uses


def test_category_rollup_covers_all_skills(conn, team):
    ov = services.team_overview(conn, team["r2"].id)
    assert sum(c.skills for c in ov.categories) == len(ov.skills)
    assert len(ov.categories) == 18


def test_person_profile(conn, team):
    p = services.person_profile(conn, team["r2"].id, team["bo"].id)
    assert p.summary == metrics.person_summary(p.rows)
    prio = dict(conn.execute("SELECT name, priority FROM skills WHERE name IN ('ggplot2', 'SQL')").fetchall())
    # Bo (Data Analyst, targets 5 and 5): ggplot2 2 -> 3 below, SQL 3 -> 2 below; ties keep catalogue order
    expected = sorted([("ggplot2", prio["ggplot2"] * 3, 0), ("SQL", prio["SQL"] * 2, 1)], key=lambda t: (-t[1], t[2]))
    assert [r.name for r in p.top_gaps] == [n for n, _, _ in expected]
    assert [r.name for r in p.top_interests] == ["ggplot2", "SQL"]  # desire 3 before 2
    assert p.previous is None and p.changes == []
    ann = services.person_profile(conn, team["r2"].id, team["ann"].id)
    assert ann.previous.round.label == "R1"
    assert [(r.name, r.prev_used, r.used) for r in ann.changes] == [("ggplot2", 2, 5)]
    with pytest.raises(services.NotFound):
        services.person_profile(conn, team["r1"].id, team["bo"].id)


# ---------- pages ----------


@pytest.fixture
def client(db_path):
    with TestClient(create_app(db_path), base_url="http://localhost", follow_redirects=False) as c:
        yield c


def test_team_page(client, team):
    page = client.get(f"/round/{team['r2'].id}/team").text
    assert "People assessed" in page and "vs R1" in page
    assert page.count("<tr>") >= 132
    first_row = lambda html: re.search(r"<tbody>\s*<tr>\s*<td><span class=\"name\">([^<]+)", html).group(1)
    assert first_row(client.get(f"/round/{team['r2'].id}/team?sort=weighted").text) in ("ggplot2", "SQL")
    page = client.get(f"/round/{team['r2'].id}/team?cat=Python+Programming").text
    assert "16 skills" in page
    page = client.get(f"/round/{team['r2'].id}/team?tag=R&sort=median_level").text
    assert 'href="?tag=R&amp;sort=median_level&amp;dir=asc"' in page  # toggles, keeps the filter
    assert client.get(f"/round/{team['r2'].id}/team?sort=nonsense&cat=nope&tag=Julia").status_code == 200
    assert client.get("/round/999/team").status_code == 404


def test_profile_page(client, team):
    page = client.get(f"/round/{team['r2'].id}/person/{team['ann'].id}/profile").text
    assert "Top" in page and "Changes since R1" in page and "ggplot2" in page
    r = client.get(f"/round/{team['r1'].id}/person/{team['bo'].id}/profile")
    assert r.status_code == 303 and r.headers["location"] == f"/round/{team['r1'].id}"


def test_demo_team_page_renders(tmp_path):
    from competence import seed

    path = tmp_path / "demo.sqlite"
    seed.create_database(path, demo=True)
    c = connect(path)
    q3 = c.execute("SELECT id FROM rounds WHERE label = '2026-Q3'").fetchone()[0]
    ov = services.team_overview(c, q3)
    c.close()
    assert ov.headline.people_assessed == 6 and ov.previous_round.label == "2026-Q2"
    with TestClient(create_app(path, demo=True), base_url="http://localhost") as client:
        page = client.get(f"/round/{q3}/team").text
        assert "vs 2026-Q2" in page and "DEMO" in page
