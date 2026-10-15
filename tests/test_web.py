import re

import pytest
from fastapi.testclient import TestClient

from competence.core import admin, services
from competence.core.db import connect
from competence.web.app import create_app, format_number, format_signed

BASE = "http://localhost"
SAME = {"Origin": BASE}


@pytest.fixture
def client(db_path):
    with TestClient(create_app(db_path), base_url=BASE, follow_redirects=False) as c:
        yield c


@pytest.fixture
def assessment(db_path):
    c = connect(db_path)
    rnd = services.create_round(c, "2026-Q4", None)
    person = services.create_person(c, "Test Person")
    role = next(r for r in services.list_active_roles(c) if r.name == "Data Scientist")
    a = services.start_assessment(c, rnd.id, person.id, role.id)
    ggplot = c.execute("SELECT id FROM skills WHERE name = 'ggplot2'").fetchone()[0]
    c.close()
    return a, ggplot


def stored(db_path, skill_id):
    c = connect(db_path)
    r = c.execute("SELECT self, agreed, desire FROM ratings WHERE skill_id = ?", (skill_id,)).fetchone()
    c.close()
    return tuple(r) if r else None


# ---------- pages and flow ----------


def test_home_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'href="/rounds">Rounds</a>' in r.text and "htmx-2.0.11.min.js" in r.text
    assert "DEMO" not in r.text


def test_demo_banner(db_path):
    with TestClient(create_app(db_path, demo=True), base_url=BASE) as c:
        assert "DEMO" in c.get("/").text


def test_full_flow_create_round_person_start_and_rate(client, db_path):
    r = client.post("/rounds", data={"label": "2026-Q4", "held_on": "2026-12-01"}, headers=SAME)
    assert r.status_code == 303
    round_url = r.headers["location"]
    assert client.post("/admin/people", data={"name": "Robin"}, headers=SAME).status_code == 303

    page = client.get(round_url).text
    start_url = re.search(r'action="(/round/\d+/person/\d+/start)"', page).group(1)
    role_id = re.search(r'<option value="(\d+)">Data Scientist</option>', page).group(1)
    r = client.post(start_url, data={"role_id": role_id}, headers=SAME)
    assert r.status_code == 303
    rate_url = r.headers["location"]

    page = client.get(rate_url).text
    assert page.count('class="skill-row"') == 132
    assert "Data Scientist" in page


def test_duplicate_round_shows_error(client):
    client.post("/rounds", data={"label": "R1"}, headers=SAME)
    r = client.post("/rounds", data={"label": "R1"}, headers=SAME)
    assert r.status_code == 400 and "already exists" in r.text


def test_rate_page_redirects_if_not_started(client, assessment, db_path):
    a, _ = assessment
    c = connect(db_path)
    other = services.create_person(c, "Not Started")
    c.close()
    r = client.get(f"/round/{a.round.id}/person/{other.id}")
    assert r.status_code == 303 and r.headers["location"] == f"/round/{a.round.id}"


def test_tag_filter(client, assessment):
    a, _ = assessment
    page = client.get(f"/round/{a.round.id}/person/{a.person.id}?tag=Python").text
    assert page.count('class="skill-row"') == 16
    page = client.get(f"/round/{a.round.id}/person/{a.person.id}?tag=Julia").text  # unknown -> all
    assert page.count('class="skill-row"') == 132
    assert 'href="?tag=Python"' in page and page.count('class="tag"') == 132


def test_tag_filter_hidden_with_fewer_than_two_tags(client, db_path, assessment):
    """A team that doesn't use tags (all blank) sees no tag filter and no badges."""
    a, _ = assessment
    c = connect(db_path)
    with c:
        c.execute("UPDATE skills SET tag = 'R and friends' WHERE tag = 'R'")
    assert services.tags(c)[0] == "R and friends"
    page = client.get(f"/round/{a.round.id}/person/{a.person.id}").text
    assert 'href="?tag=R+and+friends"' in page  # tags with spaces are encoded
    with c:
        c.execute("UPDATE skills SET tag = ''")
    c.close()
    page = client.get(f"/round/{a.round.id}/person/{a.person.id}").text
    assert 'aria-label="Tag filter"' in page and "?tag=" not in page and 'class="tag"' not in page
    team = client.get(f"/round/{a.round.id}/team").text
    assert 'aria-label="Tag filter"' not in team


# ---------- saving ratings ----------


def skill_url(a, skill_id):
    return f"/round/{a.round.id}/person/{a.person.id}/skill/{skill_id}"


def test_htmx_save_returns_updated_row(client, assessment, db_path):
    a, ggplot = assessment
    r = client.post(
        skill_url(a, ggplot),
        data={"self_level": "3", "agreed": "", "desire": "3"},
        headers={**SAME, "HX-Request": "true"},
    )
    assert r.status_code == 200
    assert r.text.lstrip().startswith('<form class="skill-row"') and "saved" in r.text
    assert re.search(r'class="num gap below">\s*-2\s*<', r.text)  # DS target for ggplot2 is 5
    assert stored(db_path, ggplot) == (3, None, 3)
    assert '<option value="3" title="Practitioner — works independently on routine tasks" selected>' in r.text
    assert '<option value="3" title="Passionate to continue learning" selected>' in r.text  # desire
    assert 'name="desire"' in r.text and '<option value="0" title="Does not' not in r.text  # 1-3 only


def test_rate_page_explains_the_levels(client, assessment, db_path):
    a, _ = assessment
    c = connect(db_path)
    admin.set_level_labels(c, {"level": {8: "Teaches the whole lab"}})
    c.close()
    page = client.get(f"/round/{a.round.id}/person/{a.person.id}").text
    legend = re.search(r'<details class="legend" open>.*?</details>', page, re.DOTALL).group(
        0
    )  # open: first assessment
    assert "Teaches the whole lab" in legend and "Passionate to continue learning" in legend
    assert 'title="Teaches the whole lab"' in page


def test_plain_form_save_redirects_back(client, assessment, db_path):
    a, ggplot = assessment
    r = client.post(skill_url(a, ggplot), data={"self_level": "5", "agreed": "", "desire": ""}, headers=SAME)
    assert r.status_code == 303
    assert r.headers["location"].endswith(f"/person/{a.person.id}#skill-{ggplot}")
    assert stored(db_path, ggplot) == (5, None, None)


@pytest.mark.parametrize("bad", ["4", "7", "-1", "abc", "5.5"])
def test_invalid_level_is_rejected_and_nothing_stored(client, assessment, db_path, bad):
    a, ggplot = assessment
    r = client.post(skill_url(a, ggplot), data={"self_level": bad}, headers={**SAME, "HX-Request": "true"})
    assert r.status_code == 422
    assert stored(db_path, ggplot) is None


def test_unknown_form_field_is_rejected(client, assessment, db_path):
    a, ggplot = assessment
    r = client.post(skill_url(a, ggplot), data={"self_level": "3", "evil": "1"}, headers=SAME)
    assert r.status_code == 422
    assert stored(db_path, ggplot) is None


def test_saving_unknown_skill_is_404(client, assessment):
    a, _ = assessment
    assert client.post(skill_url(a, 99999), data={"self_level": "3"}, headers=SAME).status_code == 404


# ---------- security ----------


def test_cross_origin_post_is_refused(client, assessment, db_path):
    a, ggplot = assessment
    r = client.post(skill_url(a, ggplot), data={"self_level": "8"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert stored(db_path, ggplot) is None


def test_post_without_origin_or_referer_is_refused(client):
    assert client.post("/rounds", data={"label": "X"}).status_code == 403


def test_post_with_same_origin_referer_is_allowed(client):
    r = client.post("/rounds", data={"label": "X"}, headers={"Referer": f"{BASE}/"})
    assert r.status_code == 303


def test_lookalike_referer_is_refused(client):
    r = client.post("/rounds", data={"label": "X"}, headers={"Referer": f"{BASE}.evil.example/"})
    assert r.status_code == 403


def test_foreign_host_header_is_refused(client):
    """DNS rebinding: a page on evil.example re-pointed at 127.0.0.1 sends Host: evil.example."""
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_html_is_escaped(client, db_path):
    client.post("/admin/people", data={"name": "<script>alert(1)</script>"}, headers=SAME)
    page = client.get("/people").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_no_api_docs_exposed(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


# ---------- pre-fill ----------


def test_rate_page_shows_prefill_source_and_last_values(client, db_path):
    c = connect(db_path)
    r1 = services.create_round(c, "R1", "2026-03-01")
    r2 = services.create_round(c, "R2", "2026-06-01")
    p = services.create_person(c, "Pat")
    ds = next(r for r in services.list_active_roles(c) if r.name == "Data Scientist")
    a1 = services.start_assessment(c, r1.id, p.id, ds.id)
    g = c.execute("SELECT id FROM skills WHERE name = 'ggplot2'").fetchone()[0]
    services.save_rating(c, a1.id, g, 3, 5, 2)
    c.close()

    assert "First assessment" in client.get(f"/round/{r1.id}/person/{p.id}").text
    assert "pre-fills from R1" in client.get(f"/round/{r2.id}").text

    r = client.post(f"/round/{r2.id}/person/{p.id}/start", data={"role_id": ds.id}, headers=SAME)
    page = client.get(r.headers["location"]).text
    assert "Pre-filled from <strong>R1</strong>" in page
    row = re.search(rf'<form class="skill-row" id="skill-{g}".*?</form>', page, re.DOTALL).group(0)
    assert re.search(r'name="self_level"[^>]*>.*?<option value="5"[^>]* selected>', row, re.DOTALL)
    assert "5·2" in row and 'class="changed"' not in row

    r = client.post(
        f"/round/{r2.id}/person/{p.id}/skill/{g}",
        data={"self_level": "8", "desire": "2"},
        headers={**SAME, "HX-Request": "true"},
    )
    assert re.search(r'name="self_level"[^>]*class="changed"', r.text)
    assert not re.search(r'name="desire"[^>]*class="changed"', r.text)


def test_app_from_env_for_reload(db_path, tmp_path, monkeypatch):
    """`app.py --reload` passes the database to uvicorn's child process through env vars."""
    from competence.web.app import ENV_BACKUPS, ENV_DB, ENV_DEMO, create_app_from_env

    monkeypatch.setenv(ENV_DB, str(db_path))
    monkeypatch.setenv(ENV_DEMO, "1")
    monkeypatch.setenv(ENV_BACKUPS, str(tmp_path / "bk"))
    app = create_app_from_env()
    assert app.state.db_path == db_path and app.state.backups == tmp_path / "bk"
    with TestClient(app, base_url=BASE) as c:
        assert "DEMO" in c.get("/").text


@pytest.mark.parametrize("bad", ["0", "4", "5", "8", "-1"])
def test_desire_outside_1_to_3_is_rejected(client, assessment, db_path, bad):
    a, ggplot = assessment
    r = client.post(
        skill_url(a, ggplot), data={"self_level": "3", "desire": bad}, headers={**SAME, "HX-Request": "true"}
    )
    assert r.status_code == 422 and stored(db_path, ggplot) is None


@pytest.mark.parametrize(
    "value, decimals, shown",
    [
        (None, 1, "–"),
        (3, 1, "3"),
        (2.5, 1, "2.5"),
        (1.04, 1, "1"),
        (0.96, 1, "1"),
        (3.25, 0, "3"),
        (3.3, 0, "3"),
    ],
)
def test_format_number_rounds_before_dropping_decimals(value, decimals, shown):
    assert format_number(value, decimals) == shown


@pytest.mark.parametrize(
    "value, decimals, shown",
    [
        (None, 1, ""),
        (0, 1, "±0"),
        (0.04, 1, "±0"),
        (-0.04, 1, "±0"),
        (1.02, 1, "+1"),
        (0.5, 1, "+0.5"),
        (-0.3, 1, "−0.3"),
        (2, 0, "+2"),
    ],
)
def test_format_signed(value, decimals, shown):
    assert format_signed(value, decimals) == shown
