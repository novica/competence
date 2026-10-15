"""Development goals — create from a gap, edit, status, progress across rounds."""

import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from competence import seed
from competence.core import services
from competence.core.db import connect
from competence.web.app import create_app

SAME = {"Origin": "http://localhost"}


def sid(conn, name):
    return conn.execute("SELECT id FROM skills WHERE name = ?", (name,)).fetchone()[0]


@pytest.fixture
def setup(conn):
    r1 = services.create_round(conn, "R1", "2026-03-01")
    r2 = services.create_round(conn, "R2", "2026-06-01")
    p = services.create_person(conn, "Pat")
    ds = next(r for r in services.list_active_roles(conn) if r.name == "Data Scientist")
    a1 = services.start_assessment(conn, r1.id, p.id, ds.id)
    g = sid(conn, "ggplot2")
    services.save_rating(conn, a1.id, g, 2, None, 2)
    return {"r1": r1, "r2": r2, "p": p, "ds": ds, "a1": a1, "g": g}


def test_create_and_progress_across_rounds(conn, setup):
    s = setup
    goal = services.create_goal(conn, s["p"].id, s["r1"].id, " Better plots ", s["g"], 5, s["r2"].id, "course")
    assert (goal.title, goal.status, goal.skill_name, goal.due_round.label) == ("Better plots", "open", "ggplot2", "R2")
    [gp] = services.list_goals(conn, s["p"].id, s["r1"].id)
    assert (gp.current, gp.previous, gp.delta, gp.reached) == (2, None, None, False)
    a2 = services.start_assessment(conn, s["r2"].id, s["p"].id, s["ds"].id)  # pre-fills 2
    services.save_rating(conn, a2.id, s["g"], 5, None, 2)
    [gp] = services.list_goals(conn, s["p"].id, s["r2"].id)
    assert (gp.current, gp.previous, gp.delta, gp.reached, gp.previous_round.label) == (5, 2, 3, True, "R1")


def test_goal_not_shown_before_the_round_it_was_set_in(conn, setup):
    s = setup
    services.create_goal(conn, s["p"].id, s["r2"].id, "Later goal")
    assert services.list_goals(conn, s["p"].id, s["r1"].id) == []
    assert len(services.list_goals(conn, s["p"].id, s["r2"].id)) == 1


def test_general_goal_has_no_progress(conn, setup):
    s = setup
    services.create_goal(conn, s["p"].id, s["r1"].id, "Present at a conference")
    [gp] = services.list_goals(conn, s["p"].id, s["r1"].id)
    assert gp.goal.skill_id is None and gp.current is None and not gp.reached


def test_validation(conn, setup):
    s = setup
    with pytest.raises(services.Invalid, match="title"):
        services.create_goal(conn, s["p"].id, s["r1"].id, "  ")
    with pytest.raises(services.Invalid, match="target level"):
        services.create_goal(conn, s["p"].id, s["r1"].id, "x", s["g"], 4)
    with pytest.raises(services.Invalid, match="due round"):
        services.create_goal(conn, s["p"].id, s["r2"].id, "x", due_round_id=s["r1"].id)
    with conn:
        conn.execute("UPDATE skills SET retired_on = '2026-01-01' WHERE name = 'Shiny'")
    with pytest.raises(services.Invalid, match="active"):
        services.create_goal(conn, s["p"].id, s["r1"].id, "x", sid(conn, "Shiny"))
    goal = services.create_goal(conn, s["p"].id, s["r1"].id, "x")
    with pytest.raises(services.Invalid, match="status"):
        services.set_goal_status(conn, goal.id, "finished")


def test_update_and_status_order(conn, setup):
    s = setup
    a = services.create_goal(conn, s["p"].id, s["r1"].id, "A")
    b = services.create_goal(conn, s["p"].id, s["r1"].id, "B")
    services.update_goal(conn, a.id, "A2", 3, s["r2"].id, "done", "did it", "note")
    services.set_goal_status(conn, b.id, "in_progress")
    got = services.get_goal(conn, a.id)
    assert (got.title, got.target_level, got.due_round.label, got.status, got.actions) == (
        "A2",
        3,
        "R2",
        "done",
        "did it",
    )
    assert [gp.goal.title for gp in services.list_goals(conn, s["p"].id, s["r1"].id)] == ["B", "A2"]  # open first


def test_demo_goal_progress():
    """Jo's accessibility goal in the demo: self 2 -> 3 -> 5, target 5, set in Q1."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "demo.sqlite"
        seed.create_database(path, demo=True)
        c = connect(path)
        jo = c.execute("SELECT id FROM people WHERE name = 'Jo (demo)'").fetchone()[0]
        rounds = {r["label"]: r["id"] for r in c.execute("SELECT id, label FROM rounds")}
        progress = [
            next(gp for gp in services.list_goals(c, jo, rounds[q]) if gp.goal.skill_name == "Accessibility (WCAG)")
            for q in ("2026-Q1", "2026-Q2", "2026-Q3")
        ]
        c.close()
    assert [(gp.current, gp.delta, gp.reached) for gp in progress] == [(2, None, False), (3, 1, False), (5, 2, True)]


# ---------- pages ----------


@pytest.fixture
def client(db_path):
    with TestClient(create_app(db_path), base_url="http://localhost", follow_redirects=False) as c:
        yield c


def test_add_goal_from_gap_prefills_and_saves(client, setup, conn):
    s = setup
    profile = client.get(f"/round/{s['r1'].id}/person/{s['p'].id}/profile").text
    new_url = f"/round/{s['r1'].id}/person/{s['p'].id}/goals/new?skill_id={s['g']}"
    assert f'href="{new_url}"' in profile  # ggplot2 is a gap (2 vs 5)
    form = client.get(new_url).text
    assert 'value="Reach level 5 in ggplot2"' in form
    assert re.search(rf'<option value="{s["g"]}" selected>', form)
    assert re.search(r'name="target_level">.*?<option value="5" selected>', form, re.DOTALL)
    r = client.post(
        f"/round/{s['r1'].id}/person/{s['p'].id}/goals",
        data={
            "title": "Reach level 5 in ggplot2",
            "skill_id": s["g"],
            "target_level": "5",
            "due_round_id": s["r2"].id,
            "actions": "Course",
            "notes": "",
        },
        headers=SAME,
    )
    assert r.status_code == 303 and r.headers["location"].endswith("/profile#goals")
    page = client.get(f"/round/{s['r1'].id}/person/{s['p'].id}/profile").text
    assert "Reach level 5 in ggplot2" in page and "<strong>2</strong> / 5" in page


def test_bad_goal_shows_form_again(client, setup):
    s = setup
    r = client.post(f"/round/{s['r2'].id}/person/{s['p'].id}/goals", data={"title": "x"}, headers=SAME)
    assert r.status_code == 404  # Pat not assessed in R2 yet
    r = client.post(
        f"/round/{s['r1'].id}/person/{s['p'].id}/goals", data={"title": "x", "target_level": "4"}, headers=SAME
    )
    assert r.status_code == 422  # 4 isn't on the scale
    r = client.post(
        f"/round/{s['r1'].id}/person/{s['p'].id}/goals", data={"title": "x", "skill_id": "99999"}, headers=SAME
    )
    assert r.status_code == 400 and "active skills" in r.text


def test_status_change_htmx_and_edit(client, setup, conn):
    s = setup
    goal = services.create_goal(conn, s["p"].id, s["r1"].id, "Plots", s["g"], 5)
    r = client.post(
        f"/goal/{goal.id}/status",
        data={"status": "in_progress", "round_id": s["r1"].id},
        headers={**SAME, "HX-Request": "true"},
    )
    assert r.status_code == 200 and r.text.lstrip().startswith(f'<tr id="goal-{goal.id}"')
    assert '<option value="in_progress" selected>' in r.text and "saved" in r.text
    edit = client.get(f"/goal/{goal.id}/edit?round={s['r1'].id}").text
    assert 'value="Plots"' in edit and "can't be changed" in edit
    r = client.post(
        f"/goal/{goal.id}",
        data={
            "title": "Plots v2",
            "target_level": "8",
            "status": "done",
            "actions": "",
            "notes": "",
            "round_id": s["r1"].id,
        },
        headers=SAME,
    )
    assert r.status_code == 303
    assert services.get_goal(conn, goal.id).status == "done"
    assert client.post(f"/goal/{goal.id}/status", data={"status": "open", "round_id": s["r1"].id}).status_code == 403
    assert client.get("/goal/999/edit?round=1").status_code == 404


def test_status_change_for_a_goal_not_shown_in_that_round(client, setup, conn):
    s = setup
    goal = services.create_goal(conn, s["p"].id, s["r2"].id, "Later goal")
    r = client.post(
        f"/goal/{goal.id}/status",
        data={"status": "done", "round_id": s["r1"].id},
        headers={**SAME, "HX-Request": "true"},
    )
    assert r.status_code == 404
    assert services.get_goal(conn, goal.id).status == "open"


def test_profile_prints_with_a_header_and_without_controls(client, setup, conn):
    s = setup
    services.create_goal(conn, s["p"].id, s["r1"].id, "Plots", s["g"], 5)
    page = client.get(f"/round/{s['r1'].id}/person/{s['p'].id}/profile").text
    assert 'onclick="window.print()"' in page
    head = re.search(r'<p class="print-only">(.*?)</p>', page, re.DOTALL).group(1)
    assert "held 2026-03-01" in head and f"printed {date.today().isoformat()}" in head
    assert '<span class="print-only">Open</span>' in page  # the goal's status as text
    assert '<form class="no-print" method="post" action="/goal/' in page  # the dropdown isn't printed
