"""Heatmap of assessed people x active skills."""

import re

import pytest
from fastapi.testclient import TestClient

from competence.core import services
from competence.web.app import create_app
from competence.web.routes.pages import heat_class


def sid(conn, name):
    return conn.execute("SELECT id FROM skills WHERE name = ?", (name,)).fetchone()[0]


@pytest.fixture
def team(conn):
    rnd = services.create_round(conn, "R1", "2026-03-01")
    roles = {r.name: r for r in services.list_active_roles(conn)}
    bo = services.create_person(conn, "Bo")
    ann = services.create_person(conn, "Ann")
    services.create_person(conn, "Cy")  # never assessed: no column
    a = services.start_assessment(conn, rnd.id, ann.id, roles["Data Scientist"].id)
    b = services.start_assessment(conn, rnd.id, bo.id, roles["Data Analyst"].id)
    g = sid(conn, "ggplot2")
    services.save_rating(conn, a.id, g, 3, 5, 2)  # used 5 (agreed)
    services.save_rating(conn, b.id, g, 2, None, None)
    return {"r": rnd, "ann": ann, "bo": bo, "g": g}


def test_heatmap_cells(conn, team):
    hm = services.heatmap(conn, team["r"].id)
    assert [a.person.name for a in hm.columns] == ["Ann", "Bo"]
    assert len(hm.rows) == 132
    row = next(r for r in hm.rows if r.skill_id == team["g"])
    ann, bo = row.cells
    assert (ann.used, ann.desire, ann.target, ann.gap, ann.competency) == (5, 2, 5, 0, 10)
    assert (bo.used, bo.desire, bo.gap, bo.competency) == (2, None, -3, None)
    sql = next(r for r in hm.rows if r.name == "SQL")
    assert sql.cells[0].used is None and sql.cells[0].target is not None  # unrated, but target known
    assert len(services.heatmap(conn, team["r"].id, "Python").rows) == 16


def test_empty_round(conn):
    rnd = services.create_round(conn, "Empty", None)
    hm = services.heatmap(conn, rnd.id)
    assert hm.columns == [] and len(hm.rows) == 132


@pytest.mark.parametrize(
    "metric, value, cls",
    [
        ("level", None, ""),
        ("level", 0, "h0"),
        ("level", 2, "h1"),
        ("level", 3, "h2"),
        ("level", 5, "h3"),
        ("level", 8, "h4"),
        ("competency", 0, "h0"),
        ("competency", 3, "h1"),
        ("competency", 4, "h2"),
        ("competency", 8, "h2"),
        ("competency", 9, "h3"),
        ("competency", 15, "h3"),
        ("competency", 16, "h4"),
        ("competency", 24, "h4"),
        ("desire", 1, "h0"),
        ("desire", 2, "h2"),
        ("desire", 3, "h4"),
        ("gap", -3, "g-far"),
        ("gap", -1, "g-below"),
        ("gap", 0, "g-ok"),
        ("gap", 3, "g-above"),
    ],
)
def test_heat_class(metric, value, cls):
    assert heat_class(metric, value) == cls


@pytest.fixture
def client(db_path):
    with TestClient(create_app(db_path), base_url="http://localhost") as c:
        yield c


def cell(page, person_id, skill_id):
    m = re.search(
        rf'<td class="cell ([^"]*)"><a href="/round/\d+/person/{person_id}#skill-{skill_id}"[^>]*>([^<]*)</a>', page
    )
    return m.group(1).strip(), m.group(2).strip()


def test_heatmap_page_metrics(client, team):
    url = f"/round/{team['r'].id}/heatmap"
    page = client.get(url).text
    assert page.count('<th scope="row"') == 132 and "Cy" not in page
    assert cell(page, team["ann"].id, team["g"]) == ("h3", "5")
    assert cell(client.get(url + "?metric=desire").text, team["ann"].id, team["g"]) == ("h2", "2")
    assert cell(client.get(url + "?metric=competency").text, team["ann"].id, team["g"]) == ("h3", "10")
    gaps = client.get(url + "?metric=gap").text
    assert cell(gaps, team["bo"].id, team["g"]) == ("g-far", "-3")
    assert cell(gaps, team["ann"].id, team["g"]) == ("g-ok", "0")
    assert cell(client.get(url + "?metric=bogus").text, team["ann"].id, team["g"]) == ("h3", "5")


def test_heatmap_cell_links_to_rating_and_filters_keep_metric(client, team):
    page = client.get(f"/round/{team['r'].id}/heatmap?metric=gap&tag=R").text
    assert page.count('<th scope="row"') == 28
    assert f'href="/round/{team["r"].id}/person/{team["ann"].id}#skill-{team["g"]}"' in page
    assert 'href="?metric=desire&amp;tag=R"' in page and 'href="?metric=gap"' in page
    rate = client.get(f"/round/{team['r'].id}/person/{team['ann'].id}").text
    assert f'id="skill-{team["g"]}"' in rate  # the anchor the cell links to


def test_heatmap_empty_and_missing_round(client, conn):
    rnd = services.create_round(conn, "Empty", None)
    assert "Nobody has been assessed" in client.get(f"/round/{rnd.id}/heatmap").text
    assert client.get("/round/999/heatmap").status_code == 404
