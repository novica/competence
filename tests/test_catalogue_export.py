"""Exporting the catalogue in use as TOML, and seeding a new database from it."""

import pytest
from fastapi.testclient import TestClient

from competence import seed
from competence.core import admin, certifications, services
from competence.core import catalogue as cat
from competence.core.db import connect
from competence.web.app import create_app


def snapshot(conn) -> dict:
    """Everything a catalogue carries, by name (ids differ between databases)."""
    return {
        "roles": [r[0] for r in conn.execute("SELECT name FROM roles WHERE retired_on IS NULL ORDER BY sort_order")],
        "skills": [
            tuple(r)
            for r in conn.execute(
                "SELECT category, name, description, tag, priority, experts_needed FROM skills"
                " WHERE retired_on IS NULL ORDER BY sort_order"
            )
        ],
        "targets": sorted(
            tuple(r)
            for r in conn.execute(
                "SELECT s.name, r.name, t.level FROM targets t JOIN skills s ON s.id = t.skill_id"
                " JOIN roles r ON r.id = t.role_id WHERE s.retired_on IS NULL AND r.retired_on IS NULL"
            )
        ),
        "labels": services.level_labels(conn),
        "certs": [
            (
                c.name,
                c.description,
                c.valid_months,
                sorted(r.name for r in admin.list_roles(conn) if r.id in c.required_by and not r.retired_on),
            )
            for c in certifications.list_certifications(conn, include_retired=False)
        ],
    }


def reseed(conn, tmp_path, name="copy"):
    text = cat.dumps(admin.catalogue_of(conn))
    path = tmp_path / f"{name}.toml"
    path.write_text(text, encoding="utf-8")
    seed.create_database(tmp_path / f"{name}.sqlite", catalogue=path)
    return connect(tmp_path / f"{name}.sqlite"), text


@pytest.mark.parametrize("name", cat.SHIPPED)
def test_shipped_catalogues_round_trip(tmp_path, name):
    seed.create_database(tmp_path / "a.sqlite", catalogue=cat.find(name))
    a = connect(tmp_path / "a.sqlite")
    b, text = reseed(a, tmp_path)
    assert snapshot(b) == snapshot(a)
    assert cat.dumps(admin.catalogue_of(b)) == text  # stable: exporting the copy gives the same file
    a.close(), b.close()


def test_admin_changes_survive_and_retired_things_are_left_out(conn, tmp_path):
    skills = admin.list_skills(conn, include_retired=False)
    s, gone = skills[0], skills[1]
    admin.update_skill(conn, s.id, s.category, s.name, s.description, s.tag, 1, 7)
    admin.retire_skill(conn, gone.id)
    roles = admin.list_roles(conn)
    admin.retire_role(conn, roles[-1].id)
    certifications.add_certification(
        conn, 'First aid "basic"', "Course\nwith a line break", 24, [roles[0].id, roles[-1].id]
    )
    admin.set_level_labels(conn, {"level": {8: "Teaches it — ÆØÅ"}, "desire": {}})
    b, text = reseed(conn, tmp_path)
    snap = snapshot(b)
    assert snap == snapshot(conn)
    assert (s.category, s.name, s.description, s.tag, 1, 7) in snap["skills"]
    assert gone.name not in [x[1] for x in snap["skills"]] and roles[-1].name not in snap["roles"]
    assert snap["certs"] == [
        ('First aid "basic"', "Course\nwith a line break", 24, [roles[0].name])
    ]  # retired role dropped
    assert "priority = 1, experts_needed = 7" in text
    b.close()


def test_category_default_only_when_every_skill_has_that_role(tmp_path):
    path = tmp_path / "lab.toml"
    path.write_text(
        'roles = [{ code = "T", name = "Technician" }, { code = "L", name = "Lead" }]\n'
        '[[categories]]\nname = "Lab"\ntargets = { T = 5 }\n'
        'skills = [{ name = "Pipetting" }, { name = "Budgets", targets = { L = 3, T = 0 } }]\n'
    )
    text = cat.dumps(cat.load(path))
    assert cat.loads(text).skills == cat.load(path).skills
    assert '{ name = "Budgets", targets = { L = 3 } }' not in text  # L can't be a default: Pipetting has none


def test_new_keys_are_checked():
    base = 'roles = [{ code = "T", name = "Technician" }]\n'
    with pytest.raises(cat.CatalogueError, match="priority"):
        cat.loads(base + '[[categories]]\nname = "Lab"\nskills = [{ name = "X", priority = 4 }]\n')
    with pytest.raises(cat.CatalogueError, match="experts_needed"):
        cat.loads(base + '[[categories]]\nname = "Lab"\nskills = [{ name = "X", experts_needed = true }]\n')
    with pytest.raises(cat.CatalogueError, match="unknown role code 'Z'"):
        cat.loads(base + '[[certifications]]\nname = "C"\nroles = ["Z"]\n')
    with pytest.raises(cat.CatalogueError, match="listed twice"):
        cat.loads(base + '[[certifications]]\nname = "C"\n[[certifications]]\nname = "C"\n')
    loaded = cat.loads(base + '[[certifications]]\nname = "C"\nvalid_months = 12\nroles = ["T"]\n')
    assert loaded.certifications == [cat.Certification("C", "", 12, ["Technician"])]


def test_role_codes_are_short_unique_and_ascii():
    assert cat._role_codes(["Front-end Developer", "Full-stack Developer", "Fagansvarlig", "Ærlig"]) == {
        "Front-end Developer": "FED",
        "Full-stack Developer": "FSD",
        "Fagansvarlig": "F",
        "Ærlig": "R",
    }
    assert cat._role_codes(["Data Analyst", "Data Architect", "!!"]) == {
        "Data Analyst": "DA",
        "Data Architect": "DA2",
        "!!": "ROLE",
    }


def test_download(db_path):
    with TestClient(create_app(db_path), base_url="http://localhost") as client:
        r = client.get("/admin/catalogue.toml")
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/toml")
        assert "attachment" in r.headers["content-disposition"]
        assert len(cat.loads(r.text).skills) == 132
        assert 'href="/admin/catalogue.toml"' in client.get("/admin").text


def test_awkward_characters_survive():
    """Quotes, backslashes, line breaks, non-ASCII and DEL (allowed in JSON strings, not in TOML ones)."""
    name = 'A "q" \\ b\nc ÆØÅ \x7f end'
    c = cat.Catalogue(["Role"], [cat.Skill("Cat", name, name, "", {"Role": 3})], {})
    assert cat.loads(cat.dumps(c)).skills == c.skills
