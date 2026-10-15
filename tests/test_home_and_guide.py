"""Steps 19-20: the front page shows the team's state, and the guide explains every number the code produces."""

import re
from datetime import date

import pytest
from fastapi.testclient import TestClient
from markupsafe import escape

from competence.core import certifications, dashboard, metrics, services
from competence.web.app import create_app

SAME = {"Origin": "http://localhost"}


@pytest.fixture
def client(db_path):
    with TestClient(create_app(db_path), base_url="http://localhost", follow_redirects=False) as c:
        yield c


@pytest.fixture
def rounds(conn):
    """Q1: Ann rated, Bo started with nothing rated, Cy not started. Q2 (newer): nobody started."""
    ds = next(r.id for r in services.list_active_roles(conn) if r.name == "Data Scientist")
    q1 = services.create_round(conn, "Q1", "2026-03-01")
    ann, bo, _ = (services.create_person(conn, n) for n in ("Ann", "Bo", "Cy"))
    a = services.start_assessment(conn, q1.id, ann.id, ds)
    services.start_assessment(conn, q1.id, bo.id, ds)
    sql = conn.execute("SELECT id FROM skills WHERE name = 'SQL'").fetchone()[0]
    services.save_rating(conn, a.id, sql, 3, None, 2)
    q2 = services.create_round(conn, "Q2", "2026-06-01")
    return q1, q2, ann


def test_dashboard_on_a_new_database(conn):
    d = dashboard.dashboard(conn)
    assert d.getting_started and d.progress is None and d.overview is None and d.skills == 132
    assert d.flagged == 0 and d.certs == 0


def test_dashboard_progress_and_state(conn, rounds):
    q1, q2, _ann = rounds
    d = dashboard.dashboard(conn)
    p = d.progress
    assert p.round == q2 and (p.assessed, p.started, p.not_started, p.people) == (0, 0, 3, 3) and not p.complete
    assert d.overview.round == q1  # newest round that has assessments
    assert d.overview.headline.people_assessed == 1 and d.flagged > 0
    assert d.people == 3
    assert not d.getting_started
    p1 = dashboard.progress(conn, q1)
    assert (p1.assessed, p1.started, p1.not_started) == (1, 1, 1)
    assert [p.round for p in dashboard.rounds_progress(conn)] == [q2, q1]


def test_front_page_getting_started(client):
    page = client.get("/").text
    assert "Getting started" in page and 'href="/guide#catalogue"' in page and "The team in" not in page


def test_front_page_state_and_progress(client, conn, rounds):
    _q1, q2, ann = rounds
    gdpr = certifications.add_certification(conn, "GDPR", "", 12, [])
    certifications.add_record(conn, ann.id, gdpr, date.today().replace(year=date.today().year - 2))  # expired
    page = client.get("/").text
    assert "Getting started" not in page
    assert "Q2 in progress:</strong> 0 of 3 assessed" in page and f'href="/round/{q2.id}"' in page
    assert "The team in Q1" in page and 'aria-label="Headline figures"' in page
    assert re.search(r"<strong>1</strong> certification missing, expired or expiring", page)
    assert "Start new round" not in page and "Add person" not in page  # those live on /rounds and /people


def test_rounds_page(client, rounds):
    _q1, q2, _ann = rounds
    page = client.get("/rounds").text
    assert "Start new round" in page and f'href="/round/{q2.id}">Q2</a>' in page
    assert re.search(r'Q1</a></td>.*?<td class="num">1 of 3</td>', page, re.DOTALL)
    assert client.post("/rounds", data={"label": "Q9"}, headers=SAME).status_code == 303
    r = client.post("/rounds", data={"label": "Q9"}, headers=SAME)
    assert r.status_code == 400 and "already exists" in r.text and "Start new round" in r.text


def test_people_page(client, conn, rounds):
    q1, _q2, ann = rounds
    services.create_goal(conn, ann.id, q1.id, "Learn window functions", None, None, None)
    rows = {pl.person.name: pl for pl in dashboard.people(conn)}
    assert rows["Ann"].latest_round == q1 and rows["Ann"].role == "Data Scientist" and rows["Ann"].open_goals == 1
    assert rows["Cy"].latest_round is None and rows["Cy"].cert_attention == 0
    page = client.get("/people").text
    assert f'href="/round/{q1.id}/person/{ann.id}/profile">Ann</a>' in page and "Cy</a>" not in page
    assert "Not assessed yet" in page and "Add person" not in page and "Certifications needing" not in page
    gdpr = certifications.add_certification(conn, "GDPR", "", 12, [])
    certifications.add_record(conn, ann.id, gdpr, date.today().replace(year=date.today().year - 2))  # expired
    assert dashboard.people(conn)[0].cert_attention == 1
    page = client.get("/people").text
    assert "Certifications needing" in page and f'href="/people/{ann.id}/certifications">1</a>' in page


def test_add_person_in_admin(client):
    r = client.post("/admin/people", data={"name": "Dee"}, headers=SAME)
    assert r.status_code == 303 and r.headers["location"] == "/admin/people"
    r = client.post("/admin/people", data={"name": "Dee"}, headers=SAME)
    assert r.status_code == 400 and "already in the list" in r.text and "Add person" in r.text
    assert client.post("/people", data={"name": "Eve"}, headers=SAME).status_code == 405


def test_guide_names_every_flag_status_and_level_text(client, conn):
    page = client.get("/guide").text
    for flag, rule in metrics.FLAG_RULES.items():
        assert f"<dt>{flag}</dt><dd>{escape(rule)}</dd>" in page
    assert list(metrics.FLAG_RULES) == [metrics.TEAM_GAP, metrics.BELOW_EXPERT_TARGET, metrics.KEY_PERSON_RISK]
    for status in certifications.STATUSES:
        assert f"<dt>{certifications.STATUS_LABELS[status]}</dt>" in page
    for scale, texts in services.level_labels(conn).items():
        assert all(str(escape(t)) in page for t in texts.values()), scale
    assert 'href="/guide">Guide</a>' in client.get("/").text


def test_guide_shows_where_the_data_is(client, db_path):
    page = client.get("/guide").text
    assert f"<code>{escape(str(db_path))}</code>" in page
    assert f"<code>{escape(str(db_path.parent / 'backups'))}</code>" in page


def test_help_links_point_at_guide_sections(client, conn, rounds):
    q1, _, ann = rounds
    ids = set(re.findall(r'id="([\w-]+)"', client.get("/guide").text))
    pages = [
        "/",
        f"/round/{q1.id}/team",
        f"/round/{q1.id}/person/{ann.id}",
        f"/round/{q1.id}/person/{ann.id}/profile",
        "/certifications",
        "/admin/targets",
    ]
    found = set()
    for url in pages:
        found |= set(re.findall(r'href="/guide#([\w-]+)"', client.get(url).text))
    assert found and found <= ids, found - ids
