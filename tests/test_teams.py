"""Several teams in one database, recorded per assessment like the role."""

import pytest
from fastapi.testclient import TestClient

from competence import seed
from competence.core import admin, certifications, dashboard, services
from competence.core.db import connect
from competence.web.app import create_app

SAME = {"Origin": "http://localhost"}


@pytest.fixture
def demo(tmp_path):
    """Demo teams: Front end (Robin, Jo; Kim until Q2) and Back end (Sam, Ari; Kim from Q3; Noor from Q2)."""
    path = tmp_path / "demo.sqlite"
    seed.create_database(path, demo=True)
    conn = connect(path)
    q1, q2, q3 = services.list_rounds(conn)[::-1]
    teams = {t.name: t.id for t in services.list_teams(conn)}
    yield {"conn": conn, "path": path, "q1": q1, "q2": q2, "q3": q3, "fe": teams["Front end"], "be": teams["Back end"]}
    conn.close()


def people_in(conn, round_id, team_id):
    return services.team_overview(conn, round_id, team_id=team_id).headline.people_assessed


def test_team_admin(conn):
    a = admin.add_team(conn, " Lab ")
    with pytest.raises(services.Invalid, match="already"):
        admin.add_team(conn, "Lab")
    with pytest.raises(services.Invalid, match="required"):
        admin.add_team(conn, " ")
    admin.add_team(conn, "Data")
    admin.rename_team(conn, a, "Wet lab")
    with pytest.raises(services.Invalid, match="already"):
        admin.rename_team(conn, a, "Data")
    admin.set_team_retired(conn, a, True)
    assert [(t.name, t.retired_on is not None) for t in services.list_teams(conn)] == [
        ("Data", False),
        ("Wet lab", True),
    ]
    assert [t.name for t in services.list_teams(conn, include_retired=False)] == ["Data"]
    with pytest.raises(services.NotFound):
        admin.rename_team(conn, 999, "X")


def test_team_per_assessment(conn):
    ds = next(r.id for r in services.list_active_roles(conn) if r.name == "Data Scientist")
    lab, data = admin.add_team(conn, "Lab"), admin.add_team(conn, "Data")
    q1, q2 = services.create_round(conn, "Q1", "2026-03-01"), services.create_round(conn, "Q2", "2026-06-01")
    p = services.create_person(conn, "Pat")
    a = services.start_assessment(conn, q1.id, p.id, ds, lab)
    assert a.team.name == "Lab"
    st = next(s for s in services.round_status(conn, q2.id) if s.person.id == p.id)
    assert st.last_team_id == lab and st.team is None  # pre-selects last round's team
    services.start_assessment(conn, q2.id, p.id, ds)  # no team is allowed
    assert services.set_assessment_team(conn, q2.id, p.id, data).team.name == "Data"
    assert services.get_assessment(conn, q1.id, p.id).team.name == "Lab"  # the other round keeps its team
    admin.set_team_retired(conn, lab, True)
    assert services.set_assessment_team(conn, q1.id, p.id, lab).team.name == "Lab"  # unchanged: still fine
    with pytest.raises(services.Invalid, match="retired"):
        services.set_assessment_team(conn, q2.id, p.id, lab)
    q3 = services.create_round(conn, "Q3", "2026-09-01")
    with pytest.raises(services.Invalid, match="retired"):
        services.start_assessment(conn, q3.id, p.id, ds, lab)
    with pytest.raises(services.NotFound):
        services.start_assessment(conn, q3.id, p.id, ds, 999)
    assert services.set_assessment_team(conn, q2.id, p.id, None).team is None
    with pytest.raises(services.NotFound):
        services.set_assessment_team(conn, q3.id, p.id, data)
    assert admin.team_counts(conn) == {lab: 1}


def test_figures_per_team_use_who_was_in_the_team_then(demo):
    conn, q1, q2, q3 = demo["conn"], demo["q1"], demo["q2"], demo["q3"]
    an, pl = demo["fe"], demo["be"]
    assert [people_in(conn, q.id, an) for q in (q1, q2, q3)] == [3, 3, 2]  # Kim leaves Front end in Q3
    assert [people_in(conn, q.id, pl) for q in (q1, q2, q3)] == [2, 3, 4]  # Noor joins in Q2, Kim in Q3
    assert [people_in(conn, q.id, None) for q in (q1, q2, q3)] == [6, 6, 6]
    ov = services.team_overview(conn, q3.id, team_id=pl)
    assert ov.previous_headline.people_assessed == 3  # Q2's Back end, not today's
    assert (
        services.team_overview(conn, q3.id).open_goals
        == services.team_overview(conn, q3.id, team_id=an).open_goals
        + services.team_overview(conn, q3.id, team_id=pl).open_goals
    )
    assert [a.person.name for a in services.heatmap(conn, q3.id, team_id=an).columns] == ["Jo (demo)", "Robin (demo)"]
    cmp = services.compare_team(conn, q1.id, q3.id, team_id=pl)
    assert (cmp.before.people_assessed, cmp.after.people_assessed) == (2, 4)


def test_certifications_front_page_and_people_per_team(demo):
    conn, an, pl = demo["conn"], demo["fe"], demo["be"]
    names = lambda t: [p.name for p in t.people]
    assert names(certifications.team_certifications(conn, team_id=an)) == ["Jo (demo)", "Robin (demo)"]
    assert names(certifications.team_certifications(conn, team_id=pl)) == [
        "Ari (demo)",
        "Kim (demo)",
        "Noor (demo)",
        "Sam (demo)",
    ]
    d = dashboard.dashboard(conn, an)
    assert d.people == 2 and [p.person.name for p in dashboard.people(conn, an)] == ["Jo (demo)", "Robin (demo)"]
    assert d.overview.headline.people_assessed == 2 and d.progress.people == 6  # progress stays the whole round's


def test_pages(demo):
    conn, q1, q3, an, pl = demo["conn"], demo["q1"], demo["q3"], demo["fe"], demo["be"]
    with TestClient(create_app(demo["path"]), base_url="http://localhost", follow_redirects=False) as c:
        for url in (
            "/",
            f"/?team={an}",
            f"/round/{q3.id}/team?team={an}&tag=General",
            f"/round/{q3.id}/heatmap?team={pl}",
            f"/compare?team={pl}",
            f"/certifications?team={an}",
            "/admin/teams",
            f"/round/{q3.id}",
        ):
            assert c.get(url).status_code == 200, url
        assert 'aria-label="Team filter"' in c.get("/").text
        assert "Front end in 2026-Q3" in c.get(f"/?team={an}").text
        note = "So the teams' figures don't add up to"  # Everyone counts every person and skill
        for url in ("/", f"/round/{q3.id}/team"):
            assert note in c.get(url).text and note not in c.get(f"{url}?team={an}").text, url
        page = c.get(f"/round/{q3.id}/team?team={an}").text
        assert f'href="?team={an}&amp;tag=' in page or f'href="?team={an}&amp;sort=' in page  # links keep the team
        assert c.get("/?team=999").status_code == 200  # unknown team = everyone
        assert c.get("/?team=x").status_code == 422
        kim = next(p.id for p in services.list_people(conn) if p.name == "Kim (demo)")
        r = c.post(f"/round/{q1.id}/person/{kim}/team", data={"team_id": str(pl)}, headers=SAME)
        assert r.status_code == 303 and r.headers["location"] == f"/round/{q1.id}#person-{kim}"
        assert services.get_assessment(conn, q1.id, kim).team.id == pl
        assert c.post(f"/round/{q1.id}/person/999/team", data={"team_id": ""}, headers=SAME).status_code == 404
        assert c.post("/admin/teams", data={"name": "Lab"}, headers=SAME).status_code == 303
        assert c.post("/admin/teams", data={"name": "Lab"}, headers=SAME).status_code == 400
        lab = next(t.id for t in services.list_teams(conn) if t.name == "Lab")
        assert c.post(f"/admin/teams/{lab}/retire", headers=SAME).status_code == 303
        assert c.post(f"/round/{q1.id}/person/{kim}/team", data={"team_id": str(lab)}, headers=SAME).status_code == 400


def test_no_team_filter_with_fewer_than_two_teams(client_plain):
    assert 'aria-label="Team filter"' not in client_plain.get("/").text


def test_start_with_team_from_round_page(client_plain, conn):
    lab = admin.add_team(conn, "Lab")
    rnd, p = services.create_round(conn, "Q1", None), services.create_person(conn, "Pat")
    role = services.list_active_roles(conn)[0].id
    assert 'name="team_id"' in client_plain.get(f"/round/{rnd.id}").text
    r = client_plain.post(f"/round/{rnd.id}/person/{p.id}/start", data={"role_id": role, "team_id": lab}, headers=SAME)
    assert r.status_code == 303 and services.get_assessment(conn, rnd.id, p.id).team.name == "Lab"
    assert "· Lab ·" in client_plain.get(f"/round/{rnd.id}/person/{p.id}").text


@pytest.fixture
def client_plain(db_path):
    with TestClient(create_app(db_path), base_url="http://localhost", follow_redirects=False) as c:
        yield c


# ---------- the categories each team uses ----------


def test_team_categories_scope_forms_and_views(demo):
    conn, q3, an, pl = demo["conn"], demo["q3"], demo["fe"], demo["be"]
    assert services.team_categories(conn, an)[:2] == ["Version Control & Collaboration", "Testing & Quality"]
    in_an = {s.category for s in services.team_overview(conn, q3.id, team_id=an).skills}
    assert in_an == set(seed.DEMO_TEAM_CATEGORIES["Front end"])
    assert len(services.team_overview(conn, q3.id).skills) == 32  # everyone: all active skills
    assert {r.category for r in services.heatmap(conn, q3.id, team_id=pl).rows} == set(
        seed.DEMO_TEAM_CATEGORIES["Back end"]
    )
    jo = next(p.id for p in services.list_people(conn) if p.name == "Jo (demo)")
    a = services.get_assessment(conn, q3.id, jo)
    assert {r.category for r in services.rating_rows(conn, a.id)} == in_an
    assert len(services.rating_rows(conn, a.id, team_only=False)) == 32
    noor = next(p.id for p in services.list_people(conn) if p.name == "Noor (demo)")
    q1 = demo["q1"]
    assert len(services.rating_rows(conn, services.get_assessment(conn, q1.id, noor).id)) == 32  # no team in Q1
    admin.set_team_categories(conn, an, [])  # none chosen = all again
    assert len(services.team_overview(conn, q3.id, team_id=an).skills) == 32
    with pytest.raises(services.Invalid, match="unknown category"):
        admin.set_team_categories(conn, an, ["Knitting"])
    with pytest.raises(services.NotFound):
        admin.set_team_categories(conn, 999, [])


def test_renaming_a_category_keeps_teams_using_it(demo):
    conn, an = demo["conn"], demo["fe"]
    skills = admin.list_skills(conn, category="User Experience")
    first, *rest = skills
    admin.update_skill(
        conn, first.id, "UX", first.name, first.description, first.tag, first.priority, first.experts_needed
    )
    assert "User Experience" in services.team_categories(conn, an)  # others still there: not a rename yet
    for s in rest:
        admin.update_skill(conn, s.id, "UX", s.name, s.description, s.tag, s.priority, s.experts_needed)
    cats = services.team_categories(conn, an)
    assert "UX" in cats and "User Experience" not in cats
    assert "UX" not in services.team_categories(conn, demo["be"])


def test_team_categories_pages(demo):
    conn, q3, an = demo["conn"], demo["q3"], demo["fe"]
    jo = next(p.id for p in services.list_people(conn) if p.name == "Jo (demo)")
    with TestClient(create_app(demo["path"]), base_url="http://localhost", follow_redirects=False) as c:
        assert "5 of 7" in c.get("/admin/teams").text
        page = c.get(f"/admin/teams/{an}/categories").text
        assert 'value="User Experience" checked' in page and 'value="Operations">' in page
        rate = c.get(f"/round/{q3.id}/person/{jo}").text
        assert "Showing the 5 categories Front end uses" in rate and "Monitoring &amp; logging" not in rate
        everything = c.get(f"/round/{q3.id}/person/{jo}?all=true").text
        assert "Showing every category" in everything and "Monitoring &amp; logging" in everything
        sql = next(s.id for s in admin.list_skills(conn, category="Back-end Development") if s.name == "SQL")
        r = c.post(
            f"/round/{q3.id}/person/{jo}/skill/{sql}", data={"self_level": "8"}, headers={**SAME, "HX-Request": "true"}
        )
        assert r.status_code == 200 and 'id="skill-' in r.text  # outside the team's categories: fine
        assert "SQL" in c.get(f"/round/{q3.id}/person/{jo}/goals/new").text  # goals can use any skill
        r = c.post(
            f"/admin/teams/{an}/categories", data={"category": ["Operations", "Testing & Quality"]}, headers=SAME
        )
        assert r.status_code == 303 and services.team_categories(conn, an) == ["Testing & Quality", "Operations"]
        r = c.post(f"/admin/teams/{an}/categories", data={"category": ["Knitting"]}, headers=SAME)
        assert r.status_code == 400 and "unknown category" in r.text
        assert c.post(f"/admin/teams/{an}/categories", data={}, headers=SAME).status_code == 303
        assert services.team_categories(conn, an) == []
        assert c.get("/admin/teams/999/categories").status_code == 404
