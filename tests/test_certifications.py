"""Certifications, their status as of a day, and the pages around them."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from competence.core import certifications as certs
from competence.core import services
from competence.web.app import create_app

TODAY = date(2026, 10, 7)
SAME = {"Origin": "http://localhost"}


@pytest.fixture
def team(conn):
    """Two assessed people (Statistician; Data Engineer who was a Statistician before) and one never assessed."""
    roles = {r.name: r.id for r in services.list_active_roles(conn)}
    q1, q2 = services.create_round(conn, "Q1", "2026-03-01"), services.create_round(conn, "Q2", "2026-06-01")
    ann, bo, cy = (services.create_person(conn, n) for n in ("Ann", "Bo", "Cy"))
    services.start_assessment(conn, q1.id, ann.id, roles["Statistician"])
    services.start_assessment(conn, q1.id, bo.id, roles["Statistician"])
    services.start_assessment(conn, q2.id, bo.id, roles["Data Engineer"])  # Bo's current role
    gdpr = certs.add_certification(conn, "GDPR training", "Yearly", 12, [roles["Statistician"]])
    cloud = certs.add_certification(conn, "Cloud practitioner", "", 36, [roles["Data Engineer"]])
    first_aid = certs.add_certification(conn, "First aid", "", None, [])
    return {"roles": roles, "ann": ann, "bo": bo, "cy": cy, "gdpr": gdpr, "cloud": cloud, "first_aid": first_aid}


def status(conn, person, cert, today=TODAY):
    return next(r.status for r in certs.person_certifications(conn, person.id, today).rows if r.cert.id == cert)


def test_add_months():
    assert certs.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert certs.add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert certs.add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)
    assert certs.add_months(date(2026, 10, 7), 36) == date(2029, 10, 7)


def test_catalogue_rules(conn, team):
    with pytest.raises(services.Invalid, match="already"):
        certs.add_certification(conn, "GDPR training", "", 12, [])
    with pytest.raises(services.Invalid, match="600 months"):
        certs.add_certification(conn, "X", "", 0, [])
    with pytest.raises(services.Invalid, match="unknown role"):
        certs.add_certification(conn, "X", "", None, [999])
    certs.update_certification(
        conn,
        team["first_aid"],
        "First aid (basic)",
        "Red Cross",
        24,
        [team["roles"]["Data Analyst"], team["roles"]["Data Analyst"]],
    )
    c = certs.get_certification(conn, team["first_aid"])
    assert (c.name, c.valid_months, c.required_by) == ("First aid (basic)", 24, (team["roles"]["Data Analyst"],))
    certs.add_record(conn, team["cy"].id, team["first_aid"], date(2026, 1, 1), today=TODAY)
    with pytest.raises(services.Invalid, match="retire it instead"):
        certs.delete_certification(conn, team["first_aid"])
    certs.delete_certification(conn, team["cloud"])
    assert team["cloud"] not in {c.id for c in certs.list_certifications(conn)}
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM role_certifications WHERE certification_id = ?", (team["cloud"],)
        ).fetchone()[0]
        == 0
    )


def test_records_and_expiry(conn, team):
    ann, gdpr = team["ann"], team["gdpr"]
    rid = certs.add_record(conn, ann.id, gdpr, date(2026, 1, 31), notes=" online ", today=TODAY)
    r = certs.person_certifications(conn, ann.id, TODAY).rows
    current = next(x for x in r if x.cert.id == gdpr).current
    assert (current.id, current.expires_on, current.notes) == (rid, "2027-01-31", "online")  # from 12 months
    certs.add_record(conn, ann.id, team["first_aid"], date(2020, 5, 1), today=TODAY)
    assert status(conn, ann, team["first_aid"]) == "valid"  # never expires
    for kwargs, message in (
        ({"obtained_on": date(2026, 11, 1)}, "future"),
        ({"obtained_on": date(2026, 5, 1), "expires_on": date(2026, 4, 1)}, "before"),
        ({"obtained_on": date(2026, 1, 31)}, "already recorded"),
    ):
        with pytest.raises(services.Invalid, match=message):
            certs.add_record(conn, ann.id, gdpr, today=TODAY, **kwargs)
    certs.retire_certification(conn, team["cloud"])
    with pytest.raises(services.Invalid, match="retired"):
        certs.add_record(conn, ann.id, team["cloud"], date(2026, 1, 1), today=TODAY)
    with pytest.raises(services.NotFound):
        certs.add_record(conn, 999, gdpr, date(2026, 1, 1), today=TODAY)


def test_status_follows_dates_renewals_and_current_role(conn, team):
    ann, bo, cy, gdpr, cloud = team["ann"], team["bo"], team["cy"], team["gdpr"], team["cloud"]
    assert status(conn, ann, gdpr) == "missing"  # Statistician needs it
    assert status(conn, bo, gdpr) is None  # Bo is a Data Engineer now: not required any more
    assert status(conn, bo, cloud) == "missing"
    assert status(conn, cy, gdpr) is None  # never assessed: no role, nothing required
    certs.add_record(conn, ann.id, gdpr, date(2025, 9, 1), today=TODAY)  # expired 2026-09-01
    assert status(conn, ann, gdpr) == "expired"
    certs.add_record(conn, ann.id, gdpr, date(2025, 11, 20), today=TODAY)  # renewal: until 2026-11-20
    assert status(conn, ann, gdpr) == "expiring"
    assert status(conn, ann, gdpr, date(2026, 9, 20)) == "valid"  # more than 60 days before
    assert status(conn, ann, gdpr, date(2026, 11, 20)) == "expiring"  # still valid on the day
    assert status(conn, ann, gdpr, date(2026, 11, 21)) == "expired"
    row = next(r for r in certs.person_certifications(conn, ann.id, TODAY).rows if r.cert.id == gdpr)
    assert row.required and [h.obtained_on for h in row.history] == ["2025-11-20", "2025-09-01"]
    certs.delete_record(conn, row.history[0].id)  # a mistake: back to the old one
    assert status(conn, ann, gdpr) == "expired"


def test_team_view(conn, team):
    ann, bo, cy = team["ann"], team["bo"], team["cy"]
    certs.add_record(conn, ann.id, team["gdpr"], date(2026, 9, 1), today=TODAY)
    certs.add_record(conn, cy.id, team["gdpr"], date(2025, 11, 20), today=TODAY)
    certs.add_record(conn, bo.id, team["first_aid"], date(2019, 1, 1), today=TODAY)
    services.create_person(conn, "Dee")
    conn.execute("UPDATE people SET active = 0 WHERE name = 'Dee'")
    conn.commit()
    t = certs.team_certifications(conn, TODAY)
    assert [p.name for p in t.people] == ["Ann", "Bo", "Cy"]  # inactive Dee left out
    by_name = {s.cert.name: s for s in t.certs}
    assert by_name["GDPR training"].counts == {"missing": 0, "expired": 0, "expiring": 1, "valid": 1}
    assert by_name["Cloud practitioner"].counts["missing"] == 1 and by_name["Cloud practitioner"].nobody_holds_it
    assert not by_name["First aid"].nobody_holds_it and not by_name["GDPR training"].nobody_holds_it
    assert [(a.person.name, a.cert.name, a.status) for a in t.attention] == [
        ("Bo", "Cloud practitioner", "missing"),
        ("Cy", "GDPR training", "expiring"),
    ]
    assert t.cells[(cy.id, team["first_aid"])] == (None, None)
    certs.retire_certification(conn, team["cloud"])
    assert "Cloud practitioner" not in {s.cert.name for s in certs.team_certifications(conn, TODAY).certs}


# ---------- pages ----------


@pytest.fixture
def client(db_path):
    with TestClient(create_app(db_path), base_url="http://localhost", follow_redirects=False) as c:
        yield c


def test_pages_with_nothing_yet(client):
    assert "No certifications yet" in client.get("/certifications").text
    assert client.get("/admin/certifications").status_code == 200
    assert client.get("/admin/certifications/new").status_code == 200
    assert client.get("/people/999/certifications").status_code == 404


def test_admin_flow(client, conn):
    ds = next(r.id for r in services.list_active_roles(conn) if r.name == "Data Scientist")
    r = client.post(
        "/admin/certifications",
        data={"name": "Security awareness", "valid_months": "12", "role_id": [str(ds)]},
        headers=SAME,
    )
    assert r.status_code == 303
    cid = certs.list_certifications(conn)[0].id
    assert certs.get_certification(conn, cid).required_by == (ds,)
    assert "Security awareness" in client.get("/admin/certifications").text
    page = client.get(f"/admin/certifications/{cid}/edit").text
    assert f'value="{ds}" checked' in page
    r = client.post(
        f"/admin/certifications/{cid}", data={"name": "Security awareness", "valid_months": ""}, headers=SAME
    )
    c = certs.get_certification(conn, cid)
    assert r.status_code == 303 and c.valid_months is None and c.required_by == ()
    assert client.post("/admin/certifications", data={"name": "Security awareness"}, headers=SAME).status_code == 400
    assert (
        client.post("/admin/certifications", data={"name": "X", "valid_months": "0"}, headers=SAME).status_code == 422
    )
    assert client.post(f"/admin/certifications/{cid}/retire", headers=SAME).status_code == 303
    assert certs.get_certification(conn, cid).retired_on is not None
    assert client.post(f"/admin/certifications/{cid}/unretire", headers=SAME).status_code == 303
    assert client.post(f"/admin/certifications/{cid}/delete", headers=SAME).status_code == 303
    assert client.post(f"/admin/certifications/{cid}/delete", headers=SAME).status_code == 404
    assert "Certifications" in client.get("/admin").text


def test_record_flow_and_profile(client, conn, team):
    ann = team["ann"]
    url = f"/people/{ann.id}/certifications"
    assert "Missing" in client.get(url).text and "(required)" in client.get(url).text
    today = date.today()  # the pages work out status as of the real today
    r = client.post(
        url,
        data={"certification_id": str(team["gdpr"]), "obtained_on": today.isoformat(), "expires_on": "", "notes": ""},
        headers=SAME,
    )
    assert r.status_code == 303 and r.headers["location"] == f"{url}#cert-{team['gdpr']}"
    assert certs.add_months(today, 12).isoformat() in client.get(url).text
    bad = client.post(url, data={"certification_id": str(team["gdpr"]), "obtained_on": today.isoformat()}, headers=SAME)
    assert bad.status_code == 400 and "already recorded" in bad.text
    assert client.post(url, data={"certification_id": "1", "obtained_on": "soon"}, headers=SAME).status_code == 422
    team_page = client.get("/certifications").text
    assert "Needs attention" in team_page and "Nobody holds it" in team_page and "Bo" in team_page
    q1 = services.list_rounds(conn)[-1]
    profile = client.get(f"/round/{q1.id}/person/{ann.id}/profile").text
    assert 'id="certifications"' in profile and "GDPR training: Valid" in profile
    rec = next(r for r in certs.person_certifications(conn, ann.id).rows if r.history).history
    r = client.post(f"/certifications/records/{rec[0].id}/delete", headers=SAME)
    assert r.status_code == 303 and r.headers["location"].startswith(url)
    assert client.post(f"/certifications/records/{rec[0].id}/delete", headers=SAME).status_code == 404
