import pytest

from competence import cli, seed
from competence.core import catalogue as cat
from competence.core import paths
from competence.core.db import connect

DATA = cat.load(cat.DEFAULT)


@pytest.fixture
def real_db(tmp_path):
    path = tmp_path / "matrix.sqlite"
    seed.create_database(path)
    conn = connect(path)
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "demo.sqlite"
    seed.create_database(path, demo=True)
    conn = connect(path)
    yield conn
    conn.close()


def count(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def test_catalogue_loads_completely(real_db):
    assert count(real_db, "SELECT COUNT(*) FROM skills") == len(DATA.skills) == 132
    assert count(real_db, "SELECT COUNT(*) FROM roles") == 9
    assert count(real_db, "SELECT COUNT(*) FROM targets") == 132 * 9
    assert count(real_db, "SELECT COUNT(DISTINCT category) FROM skills") == 18
    assert count(real_db, "SELECT COUNT(*) FROM skills WHERE tag NOT IN ('R', 'Python', 'General')") == 0
    assert count(real_db, "SELECT COUNT(*) FROM targets WHERE level NOT IN (0, 1, 2, 3, 5, 8)") == 0
    assert count(real_db, "SELECT COUNT(*) FROM people") == 0


def test_targets_match_catalogue(real_db):
    rows = real_db.execute(
        "SELECT s.name AS skill, r.name AS role, t.level FROM targets t"
        " JOIN skills s ON s.id = t.skill_id JOIN roles r ON r.id = t.role_id"
    ).fetchall()
    got = {(r["skill"], r["role"]): r["level"] for r in rows}
    for s in DATA.skills:
        for role, level in s.targets.items():
            assert got[(s.name, role)] == level


def test_priorities_and_experts_derived(real_db):
    for r in real_db.execute("SELECT priority, experts_needed FROM skills"):
        assert r["experts_needed"] == cat.EXPERTS_FOR_PRIORITY[r["priority"]]


def test_refuses_to_overwrite(tmp_path):
    path = tmp_path / "matrix.sqlite"
    path.write_bytes(b"precious")
    with pytest.raises(FileExistsError):
        seed.create_database(path)
    assert path.read_bytes() == b"precious"
    assert cli.main(["init", "--db", str(path)]) == 1


def test_other_catalogue_with_blank_targets(tmp_path):
    """A catalogue for another kind of team: skills without a target for some roles get no target row."""
    path = tmp_path / "lab.toml"
    path.write_text(
        'roles = [{ code = "T", name = "Technician" }, { code = "L", name = "Lead" }]\n'
        '[[categories]]\nname = "Lab"\ntargets = { T = 5 }\n'
        'skills = [{ name = "Pipetting" }, { name = "Budgets", targets = { L = 3, T = 0 } }]\n'
    )
    db = tmp_path / "lab.sqlite"
    assert cli.main(["init", "--db", str(db), "--catalogue", str(path)]) == 0
    conn = connect(db)
    assert count(conn, "SELECT COUNT(*) FROM targets") == 3
    rows = conn.execute("SELECT name, tag, priority, experts_needed FROM skills ORDER BY sort_order")
    assert [tuple(r) for r in rows] == [("Pipetting", "", 3, 2), ("Budgets", "", 2, 1)]
    conn.close()


def test_catalogue_can_set_level_texts(tmp_path):
    path, db = tmp_path / "lab.toml", tmp_path / "lab.sqlite"
    path.write_text(
        'roles = [{ code = "T", name = "Technician" }]\n[levels]\nlevel = { 8 = "Teaches it" }\n', encoding="utf-8"
    )
    seed.create_database(db, catalogue=path)
    conn = connect(db)
    labels = {(r[0], r[1]): r[2] for r in conn.execute("SELECT scale, level, label FROM level_labels")}
    conn.close()
    assert labels[("level", 8)] == "Teaches it"
    assert labels[("level", 5)].startswith("Advanced") and len(labels) == 9  # the rest keep the defaults


def test_empty_catalogue_seeds_one_role(tmp_path):
    db = tmp_path / "empty.sqlite"
    seed.create_database(db, catalogue=cat.CATALOGUES / "empty.toml")
    conn = connect(db)
    assert count(conn, "SELECT COUNT(*) FROM skills") == 0 and count(conn, "SELECT COUNT(*) FROM roles") == 1
    conn.close()


def test_init_by_catalogue_name_into_data_folder(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_DATA, "")  # --data sets it; this makes monkeypatch restore it afterwards
    assert cli.main(["--data", str(tmp_path), "init", "--catalogue", "empty"]) == 0
    conn = connect(tmp_path / "matrix.sqlite")
    assert count(conn, "SELECT COUNT(*) FROM roles") == 1
    conn.close()
    assert cli.main(["--data", str(tmp_path), "init", "--demo", "--catalogue", "dev"]) == 0
    assert (tmp_path / "demo.sqlite").exists()


def test_demo_flag_before_or_after_init(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_DATA, "")
    assert cli.main(["--data", str(tmp_path / "a"), "--demo", "init"]) == 0
    assert cli.main(["--data", str(tmp_path / "b"), "init", "--demo"]) == 0
    for d in "ab":
        assert (tmp_path / d / "demo.sqlite").exists() and not (tmp_path / d / "matrix.sqlite").exists()


def test_data_folder(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_DATA, str(tmp_path))
    assert paths.real_db() == tmp_path / "matrix.sqlite" and paths.backups() == tmp_path / "backups"
    monkeypatch.delenv(paths.ENV_DATA)
    assert paths.data_dir() == paths.CHECKOUT / "data"  # running from this checkout
    monkeypatch.setattr(paths, "CHECKOUT", tmp_path / "site-packages")  # installed: no pyproject.toml beside it
    assert paths.data_dir().name == "competence" and paths.data_dir() != tmp_path / "site-packages" / "data"


def test_bad_catalogue_creates_nothing(tmp_path, capsys):
    bad, db = tmp_path / "bad.toml", tmp_path / "x.sqlite"
    bad.write_text('roles = [{ code = "T", name = "Technician" }]\n[[categories]]\nname = "Lab"\ntargets = { T = 4 }\n')
    assert cli.main(["init", "--db", str(db), "--catalogue", str(bad)]) == 1
    assert "must be one of" in capsys.readouterr().err and not db.exists()
    assert cli.main(["init", "--demo", "--db", str(db), "--catalogue", str(cat.CATALOGUES / "empty.toml")]) == 1
    assert not db.exists()


def test_demo_uses_the_dev_catalogue(demo):
    assert count(demo, "SELECT COUNT(*) FROM skills") == 33
    assert [r[0] for r in demo.execute("SELECT name FROM roles ORDER BY sort_order")] == [
        "Front-end Developer",
        "Back-end Developer",
        "Full-stack Developer",
        "Tech Lead",
    ]


def test_demo_has_three_rounds_and_six_people(demo):
    assert [r[0] for r in demo.execute("SELECT label FROM rounds ORDER BY held_on")] == [
        "2026-Q1",
        "2026-Q2",
        "2026-Q3",
    ]
    assert count(demo, "SELECT COUNT(*) FROM people") == 6
    assert count(demo, "SELECT COUNT(*) FROM assessments") == 18
    assert all(name.endswith("(demo)") for (name,) in demo.execute("SELECT name FROM people"))


def test_demo_role_change_and_tech_lead(demo):
    roles = [
        r[0]
        for r in demo.execute(
            "SELECT ro.name FROM assessments a JOIN people p ON p.id = a.person_id"
            " JOIN rounds r ON r.id = a.round_id JOIN roles ro ON ro.id = a.role_id"
            " WHERE p.name = 'Kim (demo)' ORDER BY r.held_on"
        )
    ]
    assert roles == ["Front-end Developer", "Front-end Developer", "Full-stack Developer"]
    assert (
        count(demo, "SELECT COUNT(*) FROM assessments a JOIN roles r ON r.id = a.role_id WHERE r.name = 'Tech Lead'")
        == 3
    )


def _rated_rounds(conn, skill):
    return {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT r.label FROM ratings x JOIN skills s ON s.id = x.skill_id"
            " JOIN assessments a ON a.id = x.assessment_id JOIN rounds r ON r.id = a.round_id"
            " WHERE s.name = ?",
            (skill,),
        )
    }


def test_demo_added_and_retired_skills(demo):
    assert _rated_rounds(demo, seed.DEMO_ADDED_IN_Q2) == {"2026-Q2", "2026-Q3"}
    retired, on = seed.DEMO_RETIRED
    assert _rated_rounds(demo, retired) == {"2026-Q1", "2026-Q2"}
    assert count(demo, "SELECT COUNT(*) FROM skills WHERE retired_on = ?", on) == 1


def test_demo_story_and_goals(demo):
    jo_a11y = [
        r[0]
        for r in demo.execute(
            "SELECT x.self FROM ratings x JOIN skills s ON s.id = x.skill_id"
            " JOIN assessments a ON a.id = x.assessment_id JOIN people p ON p.id = a.person_id"
            " JOIN rounds r ON r.id = a.round_id WHERE p.name = 'Jo (demo)' AND s.name = 'Accessibility (WCAG)'"
            " ORDER BY r.held_on"
        )
    ]
    assert jo_a11y == [2, 3, 5]
    statuses = {r[0] for r in demo.execute("SELECT status FROM goals")}
    assert statuses == {"open", "in_progress", "done", "dropped"}
    assert count(demo, "SELECT COUNT(*) FROM goals WHERE skill_id IS NULL") == 1


def test_demo_shows_every_flag(demo):
    from competence.core import services

    q3 = services.list_rounds(demo)[0]
    h = services.team_overview(demo, q3.id).headline
    assert h.team_gaps and h.key_person_risks and h.below_expert_target


def test_demo_has_agreed_and_unassessed_ratings(demo):
    assert count(demo, "SELECT COUNT(*) FROM ratings WHERE agreed IS NOT NULL") > 0
    rated = count(demo, "SELECT COUNT(*) FROM ratings")
    possible = 18 * 33 - 6 - 6  # minus the skill added in Q2 (Q1) and the retired one (Q3)
    assert 0 < possible - rated < possible * 0.1


def test_demo_is_deterministic(tmp_path):
    a, b = tmp_path / "a.sqlite", tmp_path / "b.sqlite"
    seed.create_database(a, demo=True)
    seed.create_database(b, demo=True)
    q = "SELECT assessment_id, skill_id, self, agreed, desire FROM ratings ORDER BY 1, 2"
    ca, cb = connect(a), connect(b)
    assert ca.execute(q).fetchall() == cb.execute(q).fetchall()
    ca.close()
    cb.close()
