import pytest

from competence.core import catalogue as cat

DATA = cat.load(cat.DEFAULT)


def test_data_catalogue_size():
    assert len(DATA.roles) == 9
    assert len(DATA.categories) == 18
    assert len(DATA.skills) == 132


def test_data_catalogue_is_complete_and_on_the_scale():
    for s in DATA.skills:
        assert list(s.targets) == DATA.roles, s.name  # every role has a target, in role order
        assert set(s.targets.values()) <= set(cat.LEVELS), s.name
        assert s.tag in ("R", "Python", "General"), s.name


def test_data_catalogue_tags():
    by_tag = {t: {s.name for s in DATA.skills if s.tag == t} for t in ("R", "Python", "General")}
    assert all(s.tag == "R" for s in DATA.skills if s.category == "R Programming")
    assert all(s.tag == "Python" for s in DATA.skills if s.category == "Python Programming")
    assert "Git from R (usethis / gert)" in by_tag["R"]
    assert len(by_tag["Python"]) == 16


def test_dev_catalogue():
    dev = cat.load(cat.DEMO)
    assert dev.roles == ["Front-end Developer", "Back-end Developer", "Full-stack Developer", "Tech Lead"]
    assert (len(dev.categories), len(dev.skills)) == (7, 33)
    for s in dev.skills:
        assert list(s.targets) == dev.roles, s.name
        assert s.tag in ("General", "Front end", "Back end"), s.name


def test_empty_catalogue():
    empty = cat.load(cat.CATALOGUES / "empty.toml")
    assert empty.roles == ["Team member"] and empty.skills == []


def test_default_priority_rule():
    assert cat.default_priority([3] * 10) == 3
    assert cat.default_priority([1] * 10) == 1
    assert cat.default_priority([2] * 10) == 2
    assert cat.default_priority([]) == 2


SMALL = """
roles = [{ code = "T", name = "Technician" }, { code = "L", name = "Lead" }]

[[categories]]
name = "Lab"
targets = { T = 5 }
skills = [
  { name = "Pipetting", tag = " Wet lab " },
  { name = "Autoclave", description = "Sterilising", targets = { L = 3, T = 2 } },
]

[[categories]]
name = "Admin"
skills = [{ name = "Ordering" }]
"""


def test_category_defaults_overrides_and_blanks():
    c = cat.loads(SMALL)
    assert c.roles == ["Technician", "Lead"] and c.categories == ["Lab", "Admin"]
    pip, auto, order = c.skills
    assert pip.targets == {"Technician": 5} and pip.tag == "Wet lab" and pip.description == ""
    assert auto.targets == {"Technician": 2, "Lead": 3}  # overrides, kept in role order
    assert order.targets == {} and order.tag == ""  # no default, no override: no target


@pytest.mark.parametrize(
    "change, message",
    [
        (("Pipetting", 'Autoclave" }, { name = "Pipetting'), "already exists"),
        (("T = 5", "T = 4"), "must be one of"),
        (("T = 5", "T = true"), "must be one of"),
        (("L = 3", "X = 3"), "unknown role code 'X'"),
        (("description", "descripton"), "unknown key descripton"),
        (('name = "Lead"', 'name = "Technician"'), "listed twice"),
        (('name = "Admin"', 'name = "Lab"'), "listed twice"),
        (('name = "Ordering"', 'name = "  "'), "can't be blank"),
        (("Wet lab", "W" * 41), "at most 40"),
        (("roles = [", "roles = [ 1, "), "expected a table"),
        (("[[categories]]", "[[categories]"), "not valid TOML"),
    ],
)
def test_mistakes_are_reported(change, message):
    with pytest.raises(cat.CatalogueError, match=message):
        cat.loads(SMALL.replace(*change, 1))


def test_needs_a_role():
    with pytest.raises(cat.CatalogueError, match="at least one role"):
        cat.loads("roles = []")
    with pytest.raises(cat.CatalogueError, match="missing roles"):
        cat.loads("")


def test_unreadable_file(tmp_path):
    with pytest.raises(cat.CatalogueError, match="can't read"):
        cat.load(tmp_path / "nope.toml")


def test_level_texts():
    roles = 'roles = [{ code = "T", name = "Technician" }]\n'
    c = cat.loads(roles + '[levels]\nlevel = { 0 = "Never", 8 = " Teaches " }\ndesire = { 2 = "Keen" }\n')
    assert c.labels == {"level": {0: "Never", 8: "Teaches"}, "desire": {2: "Keen"}}
    assert cat.loads(roles).labels == {}
    for bad, message in (
        ('level = { 4 = "x" }', "unknown key 4"),
        ('mood = { 1 = "x" }', "unknown key mood"),
        ('desire = { 5 = "x" }', "unknown key 5"),
        ('desire = { 0 = "x" }', "renumber"),
        ('level = { 1 = "" }', "can't be blank"),
    ):
        with pytest.raises(cat.CatalogueError, match=message):
            cat.loads(roles + "[levels]\n" + bad + "\n")
