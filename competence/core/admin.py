"""Catalogue and housekeeping use cases: skills, roles, targets, people and rounds.

Invariants kept here:
- Skills of one category are contiguous in sort_order (every grouped view relies on it).
- Nothing with ratings is ever deleted: skills and roles are retired instead, and people deactivated.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date
from statistics import median_low

from competence.core import catalogue as cat
from competence.core import certifications, services
from competence.core.catalogue import MAX_TAG, SCALES
from competence.core.services import Invalid, NotFound, _check_level, get_person, get_round, get_team

PRIORITIES = (1, 2, 3)


@dataclass(frozen=True)
class SkillInfo:
    id: int
    category: str
    name: str
    description: str
    tag: str
    priority: int
    experts_needed: int
    sort_order: int
    retired_on: str | None
    ratings: int  # how many ratings point at it (delete only allowed at 0)
    goals: int


@dataclass(frozen=True)
class RoleInfo:
    id: int
    name: str
    sort_order: int
    retired_on: str | None
    assessments: int


@dataclass(frozen=True)
class PersonInfo:
    id: int
    name: str
    active: bool
    assessments: int


@dataclass(frozen=True)
class RoundInfo:
    id: int
    label: str
    held_on: str | None
    notes: str
    assessments: int
    goals: int


# ---------- skills ----------


def categories(conn: sqlite3.Connection) -> list[str]:
    """Categories in display order (including ones whose skills are all retired)."""
    return [r[0] for r in conn.execute("SELECT category FROM skills GROUP BY category ORDER BY MIN(sort_order)")]


def list_skills(
    conn: sqlite3.Connection, category: str | None = None, tag: str | None = None, include_retired: bool = True
) -> list[SkillInfo]:
    sql = """SELECT s.*, (SELECT COUNT(*) FROM ratings x WHERE x.skill_id = s.id) AS n_ratings,
                    (SELECT COUNT(*) FROM goals g WHERE g.skill_id = s.id) AS n_goals FROM skills s WHERE 1 = 1"""
    args = []
    if category:
        sql += " AND s.category = ?"
        args.append(category)
    if tag:
        sql += " AND s.tag = ?"
        args.append(tag)
    if not include_retired:
        sql += " AND s.retired_on IS NULL"
    return [
        SkillInfo(
            r["id"],
            r["category"],
            r["name"],
            r["description"],
            r["tag"],
            r["priority"],
            r["experts_needed"],
            r["sort_order"],
            r["retired_on"],
            r["n_ratings"],
            r["n_goals"],
        )
        for r in conn.execute(sql + " ORDER BY s.sort_order", args)
    ]


def get_skill(conn: sqlite3.Connection, skill_id: int) -> SkillInfo:
    for s in list_skills(conn):
        if s.id == skill_id:
            return s
    raise NotFound(f"no skill {skill_id}")


def suggested_targets(conn: sqlite3.Connection, category: str) -> dict[int, int]:
    """Per active role: the median target of the category's active skills (lower median, so it stays on the scale)."""
    out = {}
    for r in conn.execute("SELECT id FROM roles WHERE retired_on IS NULL"):
        levels = [
            x[0]
            for x in conn.execute(
                "SELECT t.level FROM targets t JOIN skills s ON s.id = t.skill_id"
                " WHERE t.role_id = ? AND s.category = ? AND s.retired_on IS NULL",
                (r[0], category),
            )
        ]
        if levels:
            out[r[0]] = median_low(levels)
    return out


def _clean_skill(
    category: str, name: str, description: str, tag: str, priority: int, experts_needed: int
) -> tuple[str, str, str, str]:
    category, name, description, tag = category.strip(), name.strip(), description.strip(), tag.strip()
    if not category or not name:
        raise Invalid("category and name are required")
    if len(tag) > MAX_TAG:
        raise Invalid(f"a tag can be at most {MAX_TAG} characters")
    if priority not in PRIORITIES:
        raise Invalid("priority must be 1, 2 or 3")
    if not 0 <= experts_needed <= 20:
        raise Invalid("experts needed must be between 0 and 20")
    return category, name, description, tag


def _slot_at_end_of(conn: sqlite3.Connection, category: str) -> int:
    """Make room right after the category's last skill (or at the very end for a new category); return the slot."""
    last = conn.execute("SELECT MAX(sort_order) FROM skills WHERE category = ?", (category,)).fetchone()[0]
    if last is None:
        return (conn.execute("SELECT MAX(sort_order) FROM skills").fetchone()[0] or 0) + 1
    conn.execute("UPDATE skills SET sort_order = sort_order + 1 WHERE sort_order > ?", (last,))
    return last + 1


def _unique_name_error(e: sqlite3.IntegrityError, what: str, name: str) -> Invalid:
    if "UNIQUE" in str(e):
        return Invalid(f"there is already a {what} called {name!r}")
    return Invalid(str(e))


def add_skill(
    conn: sqlite3.Connection,
    category: str,
    name: str,
    description: str,
    tag: str,
    priority: int,
    experts_needed: int,
    targets: dict[int, int | None],
    copy_ratings_from: int | None = None,
) -> tuple[int, int]:
    """Add a skill at the end of its category with the given role targets.

    copy_ratings_from: an existing skill whose ratings in the latest round that has any are copied
    into the new one, as a starting point when splitting a skill. Returns (new skill id, ratings copied).
    """
    category, name, description, tag = _clean_skill(category, name, description, tag, priority, experts_needed)
    roles = {r[0] for r in conn.execute("SELECT id FROM roles")}
    for role_id, level in targets.items():
        if role_id not in roles:
            raise Invalid(f"unknown role {role_id}")
        _check_level(level, "target")
    source_round = None
    if copy_ratings_from is not None:
        get_skill(conn, copy_ratings_from)
        source_round = conn.execute(
            """
            SELECT a.round_id FROM ratings x JOIN assessments a ON a.id = x.assessment_id JOIN rounds r ON r.id = a.round_id
            WHERE x.skill_id = ? ORDER BY COALESCE(r.held_on, '9999-12-31') DESC, r.id DESC LIMIT 1""",
            (copy_ratings_from,),
        ).fetchone()
    try:
        with conn:
            slot = _slot_at_end_of(conn, category)
            sid = conn.execute(
                "INSERT INTO skills (category, name, description, tag, priority, experts_needed, sort_order)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (category, name, description, tag, priority, experts_needed, slot),
            ).lastrowid
            conn.executemany(
                "INSERT INTO targets (role_id, skill_id, level) VALUES (?, ?, ?)",
                [(rid, sid, lvl) for rid, lvl in targets.items() if lvl is not None],
            )
            copied = 0
            if source_round is not None:
                copied = conn.execute(
                    """
                    INSERT INTO ratings (assessment_id, skill_id, self, agreed, desire)
                    SELECT x.assessment_id, ?, x.self, x.agreed, x.desire FROM ratings x
                    JOIN assessments a ON a.id = x.assessment_id
                    WHERE x.skill_id = ? AND a.round_id = ?""",
                    (sid, copy_ratings_from, source_round[0]),
                ).rowcount
    except sqlite3.IntegrityError as e:
        raise _unique_name_error(e, "skill", name) from e
    return sid, copied


def update_skill(
    conn: sqlite3.Connection,
    skill_id: int,
    category: str,
    name: str,
    description: str,
    tag: str,
    priority: int,
    experts_needed: int,
) -> None:
    """Edit a skill. Changing category moves it to the end of the new category."""
    old = get_skill(conn, skill_id)
    category, name, description, tag = _clean_skill(category, name, description, tag, priority, experts_needed)
    try:
        with conn:
            if category != old.category:
                _carry_team_categories(conn, skill_id, old.category, category)
                conn.execute("UPDATE skills SET sort_order = -1 WHERE id = ?", (skill_id,))
                slot = _slot_at_end_of(conn, category)
                conn.execute("UPDATE skills SET sort_order = ? WHERE id = ?", (slot, skill_id))
            conn.execute(
                "UPDATE skills SET category = ?, name = ?, description = ?, tag = ?, priority = ?,"
                " experts_needed = ? WHERE id = ?",
                (category, name, description, tag, priority, experts_needed, skill_id),
            )
    except sqlite3.IntegrityError as e:
        raise _unique_name_error(e, "skill", name) from e


def rename_category(conn: sqlite3.Connection, old: str, new: str) -> None:
    """Rename a category for all its skills (retired ones too) and for the teams that use it.

    The skills keep their places, so the category stays contiguous. Renaming onto another existing category
    would merge two, which is refused: move skills one by one for that.
    """
    new = new.strip()
    if not new:
        raise Invalid("a category name is required")
    if old not in categories(conn):
        raise NotFound(f"no category {old!r}")
    if new == old:
        return
    if new in categories(conn):
        raise Invalid(f"there is already a category called {new!r}; move skills one by one to merge them")
    with conn:
        conn.execute("UPDATE skills SET category = ? WHERE category = ?", (new, old))
        conn.execute(
            "INSERT OR IGNORE INTO team_categories (team_id, category)"
            " SELECT team_id, ? FROM team_categories WHERE category = ?",
            (new, old),
        )
        conn.execute("DELETE FROM team_categories WHERE category = ?", (old,))


def _carry_team_categories(conn: sqlite3.Connection, skill_id: int, old: str, new: str) -> None:
    """When the last skill leaves a category, that category is gone: a rename, done skill by skill. Teams that used
    the old name use the new one from then on, so their views don't lose those skills."""
    if conn.execute("SELECT 1 FROM skills WHERE category = ? AND id != ?", (old, skill_id)).fetchone():
        return
    conn.execute(
        "INSERT OR IGNORE INTO team_categories (team_id, category)"
        " SELECT team_id, ? FROM team_categories WHERE category = ?",
        (new, old),
    )
    conn.execute("DELETE FROM team_categories WHERE category = ?", (old,))


def move_skill(conn: sqlite3.Connection, skill_id: int, direction: str) -> None:
    """Swap with the neighbouring skill in the same category ('up' or 'down'). No-op at the edge."""
    s = get_skill(conn, skill_id)
    if direction not in ("up", "down"):
        raise Invalid("direction must be up or down")
    cmp, order = ("<", "DESC") if direction == "up" else (">", "ASC")
    other = conn.execute(
        f"SELECT id, sort_order FROM skills WHERE category = ? AND sort_order {cmp} ?"
        f" ORDER BY sort_order {order} LIMIT 1",
        (s.category, s.sort_order),
    ).fetchone()
    if other is None:
        return
    with conn:
        conn.execute("UPDATE skills SET sort_order = ? WHERE id = ?", (other["sort_order"], s.id))
        conn.execute("UPDATE skills SET sort_order = ? WHERE id = ?", (s.sort_order, other["id"]))


def retire_skill(conn: sqlite3.Connection, skill_id: int, on: date | None = None) -> None:
    get_skill(conn, skill_id)
    with conn:
        conn.execute("UPDATE skills SET retired_on = ? WHERE id = ?", ((on or date.today()).isoformat(), skill_id))


def unretire_skill(conn: sqlite3.Connection, skill_id: int) -> None:
    get_skill(conn, skill_id)
    with conn:
        conn.execute("UPDATE skills SET retired_on = NULL WHERE id = ?", (skill_id,))


def delete_skill(conn: sqlite3.Connection, skill_id: int) -> None:
    """Only for mistakes: a skill nobody has rated and no goal uses. Otherwise retire it."""
    s = get_skill(conn, skill_id)
    if s.ratings or s.goals:
        raise Invalid(f"{s.name!r} has ratings or goals; retire it instead of deleting")
    with conn:
        conn.execute("DELETE FROM targets WHERE skill_id = ?", (skill_id,))
        conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))


# ---------- roles and targets ----------


def list_roles(conn: sqlite3.Connection) -> list[RoleInfo]:
    return [
        RoleInfo(r["id"], r["name"], r["sort_order"], r["retired_on"], r["n"])
        for r in conn.execute(
            "SELECT r.*, (SELECT COUNT(*) FROM assessments a WHERE a.role_id = r.id) AS n FROM roles r ORDER BY sort_order"
        )
    ]


def _role(conn: sqlite3.Connection, role_id: int) -> RoleInfo:
    for r in list_roles(conn):
        if r.id == role_id:
            return r
    raise NotFound(f"no role {role_id}")


def add_role(conn: sqlite3.Connection, name: str, copy_targets_from: int | None = None) -> int:
    name = name.strip()
    if not name:
        raise Invalid("role name is required")
    if copy_targets_from is not None:
        _role(conn, copy_targets_from)
    try:
        with conn:
            order = (conn.execute("SELECT MAX(sort_order) FROM roles").fetchone()[0] or 0) + 1
            rid = conn.execute("INSERT INTO roles (name, sort_order) VALUES (?, ?)", (name, order)).lastrowid
            if copy_targets_from is not None:
                conn.execute(
                    "INSERT INTO targets (role_id, skill_id, level)"
                    " SELECT ?, skill_id, level FROM targets WHERE role_id = ?",
                    (rid, copy_targets_from),
                )
    except sqlite3.IntegrityError as e:
        raise _unique_name_error(e, "role", name) from e
    return rid


def rename_role(conn: sqlite3.Connection, role_id: int, name: str) -> None:
    _role(conn, role_id)
    name = name.strip()
    if not name:
        raise Invalid("role name is required")
    try:
        with conn:
            conn.execute("UPDATE roles SET name = ? WHERE id = ?", (name, role_id))
    except sqlite3.IntegrityError as e:
        raise _unique_name_error(e, "role", name) from e


def retire_role(conn: sqlite3.Connection, role_id: int, on: date | None = None) -> None:
    role = _role(conn, role_id)
    active = [r for r in list_roles(conn) if r.retired_on is None]
    if role.retired_on is None and len(active) == 1:
        raise Invalid("can't retire the last active role")
    with conn:
        conn.execute("UPDATE roles SET retired_on = ? WHERE id = ?", ((on or date.today()).isoformat(), role_id))


def unretire_role(conn: sqlite3.Connection, role_id: int) -> None:
    _role(conn, role_id)
    with conn:
        conn.execute("UPDATE roles SET retired_on = NULL WHERE id = ?", (role_id,))


def targets_for(conn: sqlite3.Connection, skill_ids: list[int]) -> dict[tuple[int, int], int]:
    """(role_id, skill_id) -> level for the given skills."""
    if not skill_ids:
        return {}
    marks = ",".join("?" * len(skill_ids))
    return {
        (r[0], r[1]): r[2]
        for r in conn.execute(f"SELECT role_id, skill_id, level FROM targets WHERE skill_id IN ({marks})", skill_ids)
    }


def set_target(conn: sqlite3.Connection, role_id: int, skill_id: int, level: int | None) -> None:
    """Set one role's target for one skill. Blank removes it ("no target", which is not the same as 0)."""
    _role(conn, role_id)
    get_skill(conn, skill_id)
    _check_level(level, "target")
    with conn:
        if level is None:
            conn.execute("DELETE FROM targets WHERE role_id = ? AND skill_id = ?", (role_id, skill_id))
        else:
            conn.execute(
                "INSERT INTO targets (role_id, skill_id, level) VALUES (?, ?, ?)"
                " ON CONFLICT (role_id, skill_id) DO UPDATE SET level = excluded.level",
                (role_id, skill_id, level),
            )


def set_category_targets(
    conn: sqlite3.Connection, role_id: int, category: str, level: int | None, tag: str | None = None
) -> int:
    """Set one role's target for every active skill in a category (only those with `tag`, if given).

    The quick way to fill targets for a new team or role; adjust the exceptions one cell at a time afterwards.
    Blank removes them. Returns how many skills it covered.
    """
    _role(conn, role_id)
    _check_level(level, "target")
    sql, args = "SELECT id FROM skills WHERE category = ? AND retired_on IS NULL", [category]
    if tag:
        sql += " AND tag = ?"
        args.append(tag)
    skill_ids = [r[0] for r in conn.execute(sql, args)]
    if not skill_ids:
        raise NotFound(f"no active skills in category {category!r}" + (f" tagged {tag!r}" if tag else ""))
    with conn:
        if level is None:
            conn.executemany(
                "DELETE FROM targets WHERE role_id = ? AND skill_id = ?", [(role_id, s) for s in skill_ids]
            )
        else:
            conn.executemany(
                "INSERT INTO targets (role_id, skill_id, level) VALUES (?, ?, ?)"
                " ON CONFLICT (role_id, skill_id) DO UPDATE SET level = excluded.level",
                [(role_id, s, level) for s in skill_ids],
            )
    return len(skill_ids)


# ---------- teams ----------


def add_team(conn: sqlite3.Connection, name: str) -> int:
    name = name.strip()
    if not name:
        raise Invalid("team name is required")
    try:
        with conn:
            return conn.execute("INSERT INTO teams (name) VALUES (?)", (name,)).lastrowid
    except sqlite3.IntegrityError as e:
        raise _unique_name_error(e, "team", name) from e


def rename_team(conn: sqlite3.Connection, team_id: int, name: str) -> None:
    get_team(conn, team_id)
    name = name.strip()
    if not name:
        raise Invalid("team name is required")
    try:
        with conn:
            conn.execute("UPDATE teams SET name = ? WHERE id = ?", (name, team_id))
    except sqlite3.IntegrityError as e:
        raise _unique_name_error(e, "team", name) from e


def set_team_retired(conn: sqlite3.Connection, team_id: int, retired: bool, on: date | None = None) -> None:
    """A retired team keeps its history and can still be filtered on, but new assessments can't use it."""
    get_team(conn, team_id)
    with conn:
        conn.execute(
            "UPDATE teams SET retired_on = ? WHERE id = ?",
            ((on or date.today()).isoformat() if retired else None, team_id),
        )


def set_team_categories(conn: sqlite3.Connection, team_id: int, chosen: list[str]) -> None:
    """The categories a team uses. None chosen = all of them."""
    get_team(conn, team_id)
    known = set(categories(conn))
    if unknown := set(chosen) - known:
        raise Invalid(f"unknown category {min(unknown)!r}")
    with conn:
        conn.execute("DELETE FROM team_categories WHERE team_id = ?", (team_id,))
        conn.executemany(
            "INSERT INTO team_categories (team_id, category) VALUES (?, ?)", [(team_id, c) for c in sorted(set(chosen))]
        )


def team_counts(conn: sqlite3.Connection) -> dict[int, int]:
    """team id -> number of assessments in it."""
    return dict(conn.execute("SELECT team_id, COUNT(*) FROM assessments WHERE team_id IS NOT NULL GROUP BY team_id"))


# ---------- level texts ----------


def set_level_labels(conn: sqlite3.Connection, labels: dict[str, dict[int, str]]) -> None:
    """Replace what the given points of the scale mean (any subset). Texts can't be blank."""
    rows = []
    for scale, texts in labels.items():
        if scale not in SCALES:
            raise Invalid(f"unknown scale {scale!r}")
        for level, text in texts.items():
            if level not in SCALES[scale]:
                raise Invalid(f"{level!r} is not on the {scale} scale")
            text = text.strip()
            if not text or len(text) > 120:
                raise Invalid(f"the text for {scale} {level} must be 1 to 120 characters")
            rows.append((text, scale, level))
    with conn:
        conn.executemany("UPDATE level_labels SET label = ? WHERE scale = ? AND level = ?", rows)


# ---------- people and rounds ----------


def list_all_people(conn: sqlite3.Connection) -> list[PersonInfo]:
    return [
        PersonInfo(r["id"], r["name"], bool(r["active"]), r["n"])
        for r in conn.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM assessments a WHERE a.person_id = p.id) AS n FROM people p ORDER BY name"
        )
    ]


def rename_person(conn: sqlite3.Connection, person_id: int, name: str) -> None:
    get_person(conn, person_id)
    name = name.strip()
    if not name:
        raise Invalid("name is required")
    try:
        with conn:
            conn.execute("UPDATE people SET name = ? WHERE id = ?", (name, person_id))
    except sqlite3.IntegrityError as e:
        raise _unique_name_error(e, "person", name) from e


def set_person_active(conn: sqlite3.Connection, person_id: int, active: bool) -> None:
    """Inactive people disappear from rounds and lists; their history stays."""
    get_person(conn, person_id)
    with conn:
        conn.execute("UPDATE people SET active = ? WHERE id = ?", (int(active), person_id))


def list_all_rounds(conn: sqlite3.Connection) -> list[RoundInfo]:
    return [
        RoundInfo(r["id"], r["label"], r["held_on"], r["notes"], r["na"], r["ng"])
        for r in conn.execute("""
        SELECT r.*, (SELECT COUNT(*) FROM assessments a WHERE a.round_id = r.id) AS na,
               (SELECT COUNT(*) FROM goals g WHERE g.created_round_id = r.id OR g.due_round_id = r.id) AS ng
        FROM rounds r ORDER BY COALESCE(held_on, '9999-12-31') DESC, id DESC""")
    ]


def update_round(conn: sqlite3.Connection, round_id: int, label: str, held_on: str | None, notes: str) -> None:
    get_round(conn, round_id)
    label = label.strip()
    if not label:
        raise Invalid("round label is required")
    try:
        with conn:
            conn.execute(
                "UPDATE rounds SET label = ?, held_on = ?, notes = ? WHERE id = ?",
                (label, held_on, notes.strip(), round_id),
            )
    except sqlite3.IntegrityError as e:
        if "UNIQUE" in str(e):
            raise Invalid(f"a round called {label!r} already exists") from e
        raise Invalid(f"invalid date {held_on!r}") from e


def delete_round(conn: sqlite3.Connection, round_id: int) -> None:
    """Only an empty round (no assessments, no goals) can be deleted, e.g. one created by mistake."""
    info = next((r for r in list_all_rounds(conn) if r.id == round_id), None)
    if info is None:
        raise NotFound(f"no round {round_id}")
    if info.assessments or info.goals:
        raise Invalid(f"round {info.label!r} has assessments or goals and can't be deleted")
    with conn:
        conn.execute("DELETE FROM rounds WHERE id = ?", (round_id,))


# ---------- catalogue export ----------


def catalogue_of(conn: sqlite3.Connection) -> cat.Catalogue:
    """The database's current catalogue: active roles, skills, targets and certifications, and the level texts.

    No people, rounds or ratings. Retired things are left out, so a new database starts from what is in use now.
    """
    roles = {
        r["id"]: r["name"]
        for r in conn.execute("SELECT id, name FROM roles WHERE retired_on IS NULL ORDER BY sort_order")
    }
    targets: dict[int, dict[str, int]] = {}
    for t in conn.execute(
        "SELECT t.skill_id, t.role_id, t.level FROM targets t JOIN roles r ON r.id = t.role_id ORDER BY r.sort_order"
    ):
        if t["role_id"] in roles:
            targets.setdefault(t["skill_id"], {})[roles[t["role_id"]]] = t["level"]
    skills = [
        cat.Skill(
            r["category"],
            r["name"],
            r["description"],
            r["tag"],
            targets.get(r["id"], {}),
            r["priority"],
            r["experts_needed"],
        )
        for r in conn.execute("SELECT * FROM skills WHERE retired_on IS NULL ORDER BY sort_order")
    ]
    certs = [
        cat.Certification(c.name, c.description, c.valid_months, [roles[rid] for rid in roles if rid in c.required_by])
        for c in certifications.list_certifications(conn, include_retired=False)
    ]
    return cat.Catalogue(list(roles.values()), skills, services.level_labels(conn), certs)
