"""Admin core: catalogue changes keep history intact and categories contiguous."""

from datetime import date

import pytest

from competence.core import admin, services


def sid(conn, name):
    return conn.execute("SELECT id FROM skills WHERE name = ?", (name,)).fetchone()[0]


def contiguous(conn) -> bool:
    cats = [r[0] for r in conn.execute("SELECT category FROM skills ORDER BY sort_order")]
    seen, prev = set(), None
    for c in cats:
        if c != prev and c in seen:
            return False
        seen.add(c)
        prev = c
    return True


@pytest.fixture
def rated(conn):
    """One rated round: Pat rated ggplot2 and Shiny."""
    rnd = services.create_round(conn, "R1", "2026-03-01")
    p = services.create_person(conn, "Pat")
    ds = next(r for r in services.list_active_roles(conn) if r.name == "Data Scientist")
    a = services.start_assessment(conn, rnd.id, p.id, ds.id)
    services.save_rating(conn, a.id, sid(conn, "ggplot2"), 3, 5, 2)
    services.save_rating(conn, a.id, sid(conn, "Shiny"), 2, None, 1)
    return {"round": rnd, "person": p, "ds": ds, "a": a}


# ---------- skills ----------


def test_suggested_targets_are_category_medians_on_the_scale(conn):
    sug = admin.suggested_targets(conn, "Python Programming")
    assert len(sug) == 9 and set(sug.values()) <= {0, 1, 2, 3, 5, 8}
    ds = next(r.id for r in services.list_active_roles(conn) if r.name == "Data Scientist")
    assert sug[ds] == 5
    assert admin.suggested_targets(conn, "No such category") == {}


def test_add_skill_goes_to_end_of_its_category(conn):
    roles = [r.id for r in services.list_active_roles(conn)]
    new, copied = admin.add_skill(
        conn, "R Programming", " Box modules ", "box::use()", "R", 2, 1, {roles[0]: 3, roles[1]: None}
    )
    assert copied == 0
    s = admin.get_skill(conn, new)
    assert s.name == "Box modules" and s.retired_on is None
    r_skills = admin.list_skills(conn, category="R Programming")
    assert r_skills[-1].id == new and contiguous(conn)
    assert admin.targets_for(conn, [new]) == {(roles[0], new): 3}  # blank = no target
    _new_cat, _ = admin.add_skill(conn, "Brand new category", "X", "", "General", 1, 0, {})
    assert admin.categories(conn)[-1] == "Brand new category" and contiguous(conn)


def test_tags_are_free_text(conn):
    sid, _ = admin.add_skill(conn, "Lab work", "Pipetting", "", "  Wet lab ", 2, 1, {})
    assert admin.get_skill(conn, sid).tag == "Wet lab"
    blank, _ = admin.add_skill(conn, "Lab work", "Autoclave", "", "", 2, 1, {})
    assert admin.get_skill(conn, blank).tag == ""
    assert services.tags(conn) == ["R", "Python", "General", "Wet lab"]  # first appearance; blank not listed
    admin.retire_skill(conn, sid)
    assert "Wet lab" not in services.tags(conn) and "Wet lab" in services.tags(conn, include_retired=True)


def test_add_skill_validation(conn):
    with pytest.raises(services.Invalid, match="already"):
        admin.add_skill(conn, "R Programming", "ggplot2", "", "R", 2, 1, {})
    with pytest.raises(services.Invalid, match="tag"):
        admin.add_skill(conn, "R Programming", "X", "", "J" * 41, 2, 1, {})
    with pytest.raises(services.Invalid, match="priority"):
        admin.add_skill(conn, "R Programming", "X", "", "R", 4, 1, {})
    with pytest.raises(services.Invalid, match="target"):
        admin.add_skill(conn, "R Programming", "X", "", "R", 2, 1, {1: 4})
    with pytest.raises(services.Invalid, match="role"):
        admin.add_skill(conn, "R Programming", "X", "", "R", 2, 1, {999: 3})


def test_split_copies_latest_round_ratings(conn, rated):
    old = sid(conn, "ggplot2")
    r2 = services.create_round(conn, "R2", "2026-06-01")
    a2 = services.start_assessment(conn, r2.id, rated["person"].id, rated["ds"].id)
    services.save_rating(conn, a2.id, old, 8, None, 2)
    new, copied = admin.add_skill(conn, "R Programming", "ggplot2 extensions", "", "R", 2, 1, {}, copy_ratings_from=old)
    assert copied == 1
    rows = conn.execute("SELECT assessment_id, self, agreed, desire FROM ratings WHERE skill_id = ?", (new,)).fetchall()
    assert [tuple(r) for r in rows] == [(a2.id, 8, None, 2)]  # R2 only, the latest
    admin.retire_skill(conn, old, date(2026, 7, 1))
    assert admin.get_skill(conn, old).ratings == 2  # history kept


def test_rename_and_recategorise_keep_ratings(conn, rated):
    g = sid(conn, "ggplot2")
    admin.update_skill(conn, g, "Data Analysis & Visualisation", "ggplot2 (R)", "plots", "R", 3, 2)
    s = admin.get_skill(conn, g)
    assert (s.name, s.category, s.priority, s.experts_needed, s.ratings) == (
        "ggplot2 (R)",
        "Data Analysis & Visualisation",
        3,
        2,
        1,
    )
    assert admin.list_skills(conn, category="Data Analysis & Visualisation")[-1].id == g
    assert contiguous(conn)
    row = next(r for r in services.rating_rows(conn, rated["a"].id) if r.skill_id == g)
    assert (row.name, row.self_level, row.agreed) == ("ggplot2 (R)", 3, 5)
    with pytest.raises(services.Invalid, match="already"):
        admin.update_skill(conn, g, "R Programming", "Shiny", "", "R", 2, 1)


def test_move_within_category(conn):
    r = admin.list_skills(conn, category="R Programming")
    first, second = r[0].id, r[1].id
    admin.move_skill(conn, second, "up")
    assert [s.id for s in admin.list_skills(conn, category="R Programming")][:2] == [second, first]
    admin.move_skill(conn, second, "up")  # already first: no-op
    assert admin.list_skills(conn, category="R Programming")[0].id == second
    last = admin.list_skills(conn, category="R Programming")[-1].id
    admin.move_skill(conn, last, "down")  # can't leave the category
    assert admin.list_skills(conn, category="R Programming")[-1].id == last
    assert contiguous(conn)


def test_retire_unretire_and_delete_rules(conn, rated):
    shiny = sid(conn, "Shiny")
    admin.retire_skill(conn, shiny, date(2026, 5, 1))
    assert admin.get_skill(conn, shiny).retired_on == "2026-05-01"
    assert shiny not in {r.skill_id for r in services.rating_rows(conn, rated["a"].id)}
    admin.unretire_skill(conn, shiny)
    assert shiny in {r.skill_id for r in services.rating_rows(conn, rated["a"].id)}
    with pytest.raises(services.Invalid, match="retire it instead"):
        admin.delete_skill(conn, shiny)
    numpy = sid(conn, "NumPy")
    admin.delete_skill(conn, numpy)
    assert conn.execute("SELECT COUNT(*) FROM targets WHERE skill_id = ?", (numpy,)).fetchone()[0] == 0
    with pytest.raises(services.NotFound):
        admin.get_skill(conn, numpy)
    services.create_goal(conn, rated["person"].id, rated["round"].id, "Learn pandas", sid(conn, "pandas / polars"))
    with pytest.raises(services.Invalid):
        admin.delete_skill(conn, sid(conn, "pandas / polars"))  # a goal uses it


# ---------- roles and targets ----------


def test_add_role_copies_targets(conn):
    ds = next(r.id for r in services.list_active_roles(conn) if r.name == "Data Scientist")
    new = admin.add_role(conn, "Data Science Lead", copy_targets_from=ds)
    ds_t = {s: l for (r, s), l in admin.targets_for(conn, [x.id for x in admin.list_skills(conn)]).items() if r == ds}
    new_t = {s: l for (r, s), l in admin.targets_for(conn, [x.id for x in admin.list_skills(conn)]).items() if r == new}
    assert new_t == ds_t and len(new_t) == 132
    assert admin.list_roles(conn)[-1].name == "Data Science Lead"
    blank = admin.add_role(conn, "Blank role")
    assert admin.targets_for(conn, [sid(conn, "SQL")]).get((blank, sid(conn, "SQL"))) is None
    with pytest.raises(services.Invalid, match="already"):
        admin.add_role(conn, "Data Scientist")


def test_rename_and_retire_roles(conn, rated):
    admin.rename_role(conn, rated["ds"].id, "Data Scientist II")
    assert services.get_assessment(conn, rated["round"].id, rated["person"].id).role.name == "Data Scientist II"
    admin.retire_role(conn, rated["ds"].id)
    assert rated["ds"].id not in {r.id for r in services.list_active_roles(conn)}
    assert services.get_assessment(conn, rated["round"].id, rated["person"].id) is not None  # past kept
    for r in services.list_active_roles(conn)[:-1]:
        admin.retire_role(conn, r.id)
    with pytest.raises(services.Invalid, match="last active role"):
        admin.retire_role(conn, services.list_active_roles(conn)[0].id)
    admin.unretire_role(conn, rated["ds"].id)


def test_set_target_changes_gap_everywhere(conn, rated):
    g = sid(conn, "ggplot2")
    admin.set_target(conn, rated["ds"].id, g, 8)
    row = next(r for r in services.rating_rows(conn, rated["a"].id) if r.skill_id == g)
    assert (row.target, row.gap) == (8, -3)
    admin.set_target(conn, rated["ds"].id, g, None)
    row = next(r for r in services.rating_rows(conn, rated["a"].id) if r.skill_id == g)
    assert (row.target, row.gap) == (None, None)
    with pytest.raises(services.Invalid):
        admin.set_target(conn, rated["ds"].id, g, 6)


# ---------- people and rounds ----------


def test_people(conn, rated):
    admin.rename_person(conn, rated["person"].id, "Patricia")
    admin.set_person_active(conn, rated["person"].id, False)
    assert services.list_people(conn) == []
    info = admin.list_all_people(conn)[0]
    assert (info.name, info.active, info.assessments) == ("Patricia", False, 1)
    assert services.get_assessment(conn, rated["round"].id, rated["person"].id) is not None


def test_rounds(conn, rated):
    admin.update_round(conn, rated["round"].id, "2026-Q1", "2026-03-02", "kick-off")
    info = admin.list_all_rounds(conn)[0]
    assert (info.label, info.held_on, info.notes, info.assessments) == ("2026-Q1", "2026-03-02", "kick-off", 1)
    with pytest.raises(services.Invalid, match="can't be deleted"):
        admin.delete_round(conn, rated["round"].id)
    empty = services.create_round(conn, "Oops", None)
    admin.delete_round(conn, empty.id)
    assert [r.label for r in admin.list_all_rounds(conn)] == ["2026-Q1"]
    with pytest.raises(services.Invalid, match="date"):
        admin.update_round(conn, rated["round"].id, "2026-Q1", "2026-02-30", "")


def test_level_labels(conn):
    labels = services.level_labels(conn)
    assert sorted(labels) == ["desire", "level"] and list(labels["level"]) == [0, 1, 2, 3, 5, 8]
    assert labels["desire"] == {
        1: "Does not interest me",
        2: "Happy to perform this work",
        3: "Passionate to continue learning",
    }  # the article's three, 1-3
    assert labels["level"][3].startswith("Practitioner")  # workbook defaults
    admin.set_level_labels(conn, {"level": {3: "  Does it alone  "}})
    assert services.level_labels(conn)["level"][3] == "Does it alone"
    assert services.level_labels(conn)["desire"] == labels["desire"]  # untouched
    for bad, message in (
        ({"level": {4: "x"}}, "scale"),
        ({"level": {3: " "}}, "1 to 120"),
        ({"mood": {3: "x"}}, "unknown scale"),
    ):
        with pytest.raises(services.Invalid, match=message):
            admin.set_level_labels(conn, bad)
    assert services.level_labels(conn)["level"][3] == "Does it alone"


def test_set_category_targets(conn):
    roles = {r.name: r.id for r in admin.list_roles(conn)}
    da = roles["Data Analyst"]
    r_skills = [s.id for s in admin.list_skills(conn, category="R Programming")]
    admin.retire_skill(conn, r_skills[-1])
    assert admin.set_category_targets(conn, da, "R Programming", 8) == len(r_skills) - 1  # active ones only
    t = admin.targets_for(conn, r_skills)
    assert all(t[(da, s)] == 8 for s in r_skills[:-1]) and t[(da, r_skills[-1])] != 8
    assert all(t[(roles["Data Scientist"], s)] != 8 for s in r_skills[:2])  # other roles untouched
    gen = [s.id for s in admin.list_skills(conn, category="Git (Version Control)", tag="R")]
    assert admin.set_category_targets(conn, da, "Git (Version Control)", None, tag="R") == len(gen) >= 1
    assert all((da, s) not in admin.targets_for(conn, gen) for s in gen)  # blank = no target
    with pytest.raises(services.Invalid):
        admin.set_category_targets(conn, da, "R Programming", 4)
    with pytest.raises(services.NotFound):
        admin.set_category_targets(conn, da, "No such category", 3)
    with pytest.raises(services.NotFound):
        admin.set_category_targets(conn, 999, "R Programming", 3)


def test_desire_texts_only_for_0_to_2(conn):
    with pytest.raises(services.Invalid, match="not on the desire scale"):
        admin.set_level_labels(conn, {"desire": {5: "Keen"}})
    admin.set_level_labels(conn, {"desire": {2: "Wants to lead it"}, "level": {5: "Advanced"}})
    assert services.level_labels(conn)["desire"][2] == "Wants to lead it"


def test_rename_category_keeps_order_retired_skills_and_teams(conn):
    before = admin.categories(conn)
    old = before[1]
    ids = [s.id for s in admin.list_skills(conn, old)]
    admin.retire_skill(conn, ids[0])
    team = admin.add_team(conn, "Lab")
    admin.set_team_categories(conn, team, [old])
    admin.rename_category(conn, old, "  Quality  ")
    assert admin.categories(conn) == [("Quality" if c == old else c) for c in before]  # same place
    assert [s.id for s in admin.list_skills(conn, "Quality")] == ids  # retired one included
    assert services.team_categories(conn, team) == ["Quality"]
    with pytest.raises(services.Invalid, match="already a category"):
        admin.rename_category(conn, "Quality", before[0])
    with pytest.raises(services.NotFound):
        admin.rename_category(conn, "Nope", "X")
    with pytest.raises(services.Invalid, match="required"):
        admin.rename_category(conn, "Quality", "  ")
    admin.rename_category(conn, "Quality", "Quality")  # no-op
