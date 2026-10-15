"""Admin pages: forms post, validate, redirect safely and change the data."""

import re

import pytest
from fastapi.testclient import TestClient

from competence.core import admin, services
from competence.core.db import connect
from competence.web.app import create_app
from competence.web.routes.admin import back_to

SAME = {"Origin": "http://localhost"}
HX = {**SAME, "HX-Request": "true"}


@pytest.fixture
def client(db_path):
    with TestClient(create_app(db_path), base_url="http://localhost", follow_redirects=False) as c:
        yield c


def sid(conn, name):
    return conn.execute("SELECT id FROM skills WHERE name = ?", (name,)).fetchone()[0]


def test_pages_render(client):
    for url in (
        "/admin",
        "/admin/skills",
        "/admin/skills?cat=R+Programming&tag=R&show=active",
        "/admin/skills/new",
        "/admin/skills/new?category=Databases",
        "/admin/roles",
        "/admin/targets",
        "/admin/targets?cat=all",
        "/admin/people",
        "/admin/rounds",
    ):
        assert client.get(url).status_code == 200, url
    assert "132 active in 18 categories" in client.get("/admin").text


def test_new_skill_form_prefills_suggested_targets(client, conn):
    page = client.get("/admin/skills/new?category=Python+Programming").text
    ds = next(r.id for r in services.list_active_roles(conn) if r.name == "Data Scientist")
    block = re.search(
        rf'name="target_role" value="{ds}">\s*<select name="target_level">(.*?)</select>', page, re.DOTALL
    ).group(1)
    assert '<option value="5" selected>' in block


def test_add_skill_with_targets_and_split(client, conn):
    roles = [r.id for r in services.list_active_roles(conn)]
    r = client.post(
        "/admin/skills",
        data={
            "category": "R Programming",
            "name": "Box modules",
            "description": "box",
            "tag": "R",
            "priority": "2",
            "experts_needed": "1",
            "copy_ratings_from": "",
            "target_role": [str(x) for x in roles],
            "target_level": ["3"] + [""] * (len(roles) - 1),
        },
        headers=SAME,
    )
    assert r.status_code == 303 and "#skill-" in r.headers["location"]
    new = sid(conn, "Box modules")
    assert admin.targets_for(conn, [new]) == {(roles[0], new): 3}
    r = client.post(
        "/admin/skills",
        data={"category": "R Programming", "name": "Box modules", "tag": "R", "priority": "2", "experts_needed": "1"},
        headers=SAME,
    )
    assert r.status_code == 400 and "already" in r.text
    r = client.post(
        "/admin/skills",
        data={"category": "R", "name": "X", "tag": "R", "priority": "5", "experts_needed": "1"},
        headers=SAME,
    )
    assert r.status_code == 422
    r = client.post(
        "/admin/skills",
        data={
            "category": "R",
            "name": "X",
            "tag": "R",
            "priority": "2",
            "experts_needed": "1",
            "target_role": ["1", "2"],
            "target_level": ["3"],
        },
        headers=SAME,
    )
    assert r.status_code == 400 and "line up" in r.text


def test_edit_move_retire_delete(client, conn):
    g = sid(conn, "ggplot2")
    r = client.post(
        f"/admin/skills/{g}",
        data={
            "category": "R Programming",
            "name": "ggplot2!",
            "description": "",
            "tag": "R",
            "priority": "3",
            "experts_needed": "2",
        },
        headers=SAME,
    )
    assert r.status_code == 303 and admin.get_skill(conn, g).name == "ggplot2!"
    before = [s.id for s in admin.list_skills(conn, category="R Programming")].index(g)
    r = client.post(
        f"/admin/skills/{g}/move", data={"direction": "up", "back": "/admin/skills?cat=R+Programming"}, headers=SAME
    )
    assert r.headers["location"] == f"/admin/skills?cat=R+Programming#skill-{g}"
    assert [s.id for s in admin.list_skills(conn, category="R Programming")].index(g) == before - 1
    client.post(f"/admin/skills/{g}/retire", data={"back": ""}, headers=SAME)
    assert admin.get_skill(conn, g).retired_on is not None
    client.post(f"/admin/skills/{g}/unretire", data={"back": ""}, headers=SAME)
    assert admin.get_skill(conn, g).retired_on is None
    numpy = sid(conn, "NumPy")
    assert client.post(f"/admin/skills/{numpy}/delete", data={"back": ""}, headers=SAME).status_code == 303
    assert numpy not in {s.id for s in admin.list_skills(conn)}
    assert client.post("/admin/skills/99999/retire", data={"back": ""}, headers=SAME).status_code == 404


def test_delete_refused_when_rated(client, conn):
    rnd = services.create_round(conn, "R1", None)
    p = services.create_person(conn, "Pat")
    a = services.start_assessment(conn, rnd.id, p.id, services.list_active_roles(conn)[0].id)
    services.save_rating(conn, a.id, sid(conn, "SQL"), 3, None, None)
    r = client.post(f"/admin/skills/{sid(conn, 'SQL')}/delete", data={"back": ""}, headers=SAME)
    assert r.status_code == 400 and "retire it instead" in r.text


def test_roles_pages(client, conn):
    ds = next(r for r in services.list_active_roles(conn) if r.name == "Data Scientist")
    assert (
        client.post("/admin/roles", data={"name": "Analyst II", "copy_targets_from": ds.id}, headers=SAME).status_code
        == 303
    )
    new = next(r for r in admin.list_roles(conn) if r.name == "Analyst II")
    assert (
        len([1 for (rid, _) in admin.targets_for(conn, [s.id for s in admin.list_skills(conn)]) if rid == new.id])
        == 132
    )
    assert client.post(f"/admin/roles/{new.id}", data={"name": "Analyst 2"}, headers=SAME).status_code == 303
    r = client.post(f"/admin/roles/{new.id}", data={"name": "Data Scientist"}, headers=SAME)
    assert r.status_code == 400 and "already" in r.text
    assert client.post(f"/admin/roles/{new.id}/retire", headers=SAME).status_code == 303
    assert new.id not in {r.id for r in services.list_active_roles(conn)}
    assert client.post(f"/admin/roles/{new.id}/unretire", headers=SAME).status_code == 303


def test_targets_grid_htmx(client, conn):
    ds = next(r for r in services.list_active_roles(conn) if r.name == "Data Scientist")
    g = sid(conn, "ggplot2")
    page = client.get("/admin/targets?cat=R+Programming").text
    assert f'id="t-{ds.id}-{g}"' in page
    r = client.post(f"/admin/targets/{ds.id}/{g}", data={"level": "8", "back": "/admin/targets"}, headers=HX)
    assert r.status_code == 200 and r.text.lstrip().startswith(f'<td class="target-cell" id="t-{ds.id}-{g}"')
    assert '<option value="8" selected>' in r.text
    assert admin.targets_for(conn, [g])[(ds.id, g)] == 8
    r = client.post(f"/admin/targets/{ds.id}/{g}", data={"level": "", "back": "/admin/targets?cat=x"}, headers=SAME)
    assert r.status_code == 303 and r.headers["location"] == f"/admin/targets?cat=x#skill-{g}"
    assert (ds.id, g) not in admin.targets_for(conn, [g])
    assert client.post(f"/admin/targets/{ds.id}/{g}", data={"level": "4"}, headers=SAME).status_code == 422


def test_people_and_rounds_pages(client, conn):
    p = services.create_person(conn, "Pat")
    assert client.post(f"/admin/people/{p.id}", data={"name": "Patricia"}, headers=SAME).status_code == 303
    assert client.post(f"/admin/people/{p.id}/active", data={"active": "false"}, headers=SAME).status_code == 303
    assert services.list_people(conn) == []
    assert "Reactivate" in client.get("/admin/people").text
    rnd = services.create_round(conn, "Oops", None)
    r = client.post(
        f"/admin/rounds/{rnd.id}", data={"label": "2026-Q4", "held_on": "2026-12-01", "notes": "x"}, headers=SAME
    )
    assert r.status_code == 303 and services.get_round(conn, rnd.id).held_on == "2026-12-01"
    assert client.post(f"/admin/rounds/{rnd.id}/delete", headers=SAME).status_code == 303
    assert services.list_rounds(conn) == []


def test_back_field_cannot_redirect_elsewhere():
    assert back_to("/admin/skills?cat=x", "/d") == "/admin/skills?cat=x"
    for evil in ("https://evil.example", "//evil.example", "/admin//evil.example", "/admin\\..\\x", "/", ""):
        assert back_to(evil, "/d") == "/d", evil


def test_confirm_text_is_safely_escaped(client, conn):
    admin.add_skill(conn, "R Programming", "x');alert(1);//", "", "R", 1, 0, {})
    page = client.get("/admin/skills?cat=R+Programming").text
    assert "x');alert(1);//" not in page
    assert "x\\u0027);alert(1);//" in page


def test_admin_posts_need_same_origin(client, conn):
    assert client.post(f"/admin/skills/{sid(conn, 'SQL')}/retire", data={"back": ""}).status_code == 403
    assert admin.get_skill(conn, sid(conn, "SQL")).retired_on is None


def test_level_texts_page(client, conn):
    page = client.get("/admin/levels").text
    assert 'name="level_8" value="Expert — go-to person, sets standards, teaches"' in page
    form = {f"level_{p}": f"level {p}" for p in (0, 1, 2, 3, 5, 8)} | {f"desire_{p}": f"desire {p}" for p in (1, 2, 3)}
    r = client.post("/admin/levels", data=form, headers=SAME)
    assert r.status_code == 303 and r.headers["location"] == "/admin/levels?saved=true"
    assert services.level_labels(conn)["desire"][2] == "desire 2"
    assert client.post("/admin/levels", data={**form, "desire_0": "x"}, headers=SAME).status_code == 422
    assert client.post("/admin/levels", data={**form, "level_3": " "}, headers=SAME).status_code == 422
    assert client.post("/admin/levels", data={**form, "level_4": "x"}, headers=SAME).status_code == 422
    assert "Level texts" in client.get("/admin").text


def test_set_category_targets_from_grid(client, conn):
    page = client.get("/admin/targets?cat=Databases").text
    assert 'class="bulk"' in page and "Set all" in page
    assert 'class="bulk"' not in client.get("/admin/targets?cat=all").text
    st = next(r.id for r in services.list_active_roles(conn) if r.name == "Statistician")
    back = "/admin/targets?cat=Databases"
    r = client.post(
        f"/admin/targets/{st}", data={"category": "Databases", "tag": "", "level": "5", "back": back}, headers=SAME
    )
    assert r.status_code == 303 and r.headers["location"] == back
    ids = [s.id for s in admin.list_skills(conn, category="Databases")]
    assert {lvl for (role, _), lvl in admin.targets_for(conn, ids).items() if role == st} == {5}
    r = client.post(
        f"/admin/targets/{st}", data={"category": "Databases", "level": "5", "back": "//evil.example"}, headers=SAME
    )
    assert r.headers["location"] == "/admin/targets"
    assert client.post(f"/admin/targets/{st}", data={"category": "Nope", "level": "5"}, headers=SAME).status_code == 404


def test_set_all_backs_up_first(client, conn, db_path):
    """The backup taken just before "Set all" still has the targets it overwrote."""
    st = next(r.id for r in services.list_active_roles(conn) if r.name == "Statistician")
    sql = "SELECT t.level FROM targets t JOIN skills s ON s.id = t.skill_id WHERE t.role_id = ? AND s.category = ?"
    before = [r[0] for r in conn.execute(sql, (st, "Databases"))]
    client.post(f"/admin/targets/{st}", data={"category": "Databases", "level": "8"}, headers=SAME)
    backups = list((db_path.parent / "backups").glob("matrix-*.sqlite"))
    assert len(backups) == 1
    old = connect(backups[0])
    assert [r[0] for r in old.execute(sql, (st, "Databases"))] == before != [8] * len(before)
    old.close()


def test_move_arrows_only_when_every_skill_is_shown(client):
    """With a tag filter or retired skills hidden, a move could swap with a skill the page doesn't show."""
    assert 'aria-label="Move' in client.get("/admin/skills").text
    for query in ("?show=active", "?tag=Python"):
        page = client.get("/admin/skills" + query).text
        assert 'aria-label="Move' not in page and "To change the order" in page, query


def test_rename_category_page(client, conn):
    old, other = admin.categories(conn)[:2]
    page = client.get("/admin/skills").text
    assert f'aria-label="Rename {old}"' in page
    r = client.post("/admin/categories/rename", data={"old": old, "new": "Renamed"}, headers=SAME)
    assert r.status_code == 303 and r.headers["location"] == "/admin/skills?cat=Renamed"
    assert "Renamed" in admin.categories(conn) and old not in admin.categories(conn)
    r = client.post("/admin/categories/rename", data={"old": "Renamed", "new": other}, headers=SAME)
    assert r.status_code == 400 and "already a category" in r.text
    assert client.post("/admin/categories/rename", data={"old": "Nope", "new": "X"}, headers=SAME).status_code == 404
