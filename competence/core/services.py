"""Use cases. Each takes an open connection, validates its input and commits its own changes."""

import sqlite3

from competence.core import metrics
from competence.core.catalogue import DESIRES, LEVELS
from competence.core.models import (
    GOAL_STATUSES,
    Assessment,
    Goal,
    GoalProgress,
    HeatCell,
    Heatmap,
    HeatRow,
    Person,
    PersonComparison,
    PersonProfile,
    PersonSkillComparison,
    PersonStatus,
    RatingRow,
    Role,
    Round,
    SkillComparison,
    SkillOverview,
    Team,
    TeamComparison,
    TeamOverview,
)


class NotFound(LookupError):
    pass


class Invalid(ValueError):
    pass


def _check_desire(value: int | None) -> None:
    if value is not None and value not in DESIRES:
        raise Invalid(f"desire must be one of {', '.join(map(str, DESIRES))} or blank, not {value!r}")


def _check_level(value: int | None, what: str) -> None:
    if value is not None and value not in LEVELS:
        raise Invalid(f"{what} must be one of {', '.join(map(str, LEVELS))} or blank, not {value!r}")


def level_labels(conn: sqlite3.Connection) -> dict[str, dict[int, str]]:
    """What each point of the scale means: {"level": {0: "None — ...", ...}, "desire": {...}}."""
    out: dict[str, dict[int, str]] = {}
    for r in conn.execute("SELECT scale, level, label FROM level_labels ORDER BY scale, level"):
        out.setdefault(r["scale"], {})[r["level"]] = r["label"]
    return out


# ---------- rounds and people ----------


def list_rounds(conn: sqlite3.Connection) -> list[Round]:
    """Newest first; rounds without a date count as newest."""
    return [
        Round(r["id"], r["label"], r["held_on"])
        for r in conn.execute("SELECT * FROM rounds ORDER BY COALESCE(held_on, '9999-12-31') DESC, id DESC")
    ]


def get_round(conn: sqlite3.Connection, round_id: int) -> Round:
    r = conn.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)).fetchone()
    if r is None:
        raise NotFound(f"no round {round_id}")
    return Round(r["id"], r["label"], r["held_on"])


def create_round(conn: sqlite3.Connection, label: str, held_on: str | None) -> Round:
    label = label.strip()
    if not label:
        raise Invalid("round label is required")
    try:
        with conn:
            rid = conn.execute("INSERT INTO rounds (label, held_on) VALUES (?, ?)", (label, held_on)).lastrowid
    except sqlite3.IntegrityError as e:
        if "UNIQUE" in str(e):
            raise Invalid(f"a round called {label!r} already exists") from e
        raise Invalid(f"invalid date {held_on!r}") from e
    return get_round(conn, rid)


def list_people(conn: sqlite3.Connection) -> list[Person]:
    return [Person(r["id"], r["name"]) for r in conn.execute("SELECT * FROM people WHERE active = 1 ORDER BY name")]


def get_person(conn: sqlite3.Connection, person_id: int) -> Person:
    r = conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
    if r is None:
        raise NotFound(f"no person {person_id}")
    return Person(r["id"], r["name"])


def create_person(conn: sqlite3.Connection, name: str) -> Person:
    name = name.strip()
    if not name:
        raise Invalid("name is required")
    try:
        with conn:
            pid = conn.execute("INSERT INTO people (name) VALUES (?)", (name,)).lastrowid
    except sqlite3.IntegrityError as e:
        raise Invalid(f"{name!r} is already in the list") from e
    return get_person(conn, pid)


def list_active_roles(conn: sqlite3.Connection) -> list[Role]:
    return [
        Role(r["id"], r["name"])
        for r in conn.execute("SELECT * FROM roles WHERE retired_on IS NULL ORDER BY sort_order")
    ]


# ---------- teams ----------


def list_teams(conn: sqlite3.Connection, include_retired: bool = True) -> list[Team]:
    sql = "SELECT * FROM teams" + ("" if include_retired else " WHERE retired_on IS NULL")
    return [Team(r["id"], r["name"], r["retired_on"]) for r in conn.execute(sql + " ORDER BY name COLLATE NOCASE")]


def get_team(conn: sqlite3.Connection, team_id: int) -> Team:
    r = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    if r is None:
        raise NotFound(f"no team {team_id}")
    return Team(r["id"], r["name"], r["retired_on"])


def _check_team(conn: sqlite3.Connection, team_id: int | None) -> None:
    """A new or changed assessment can only be in an active team (or none)."""
    if team_id is not None and get_team(conn, team_id).retired_on is not None:
        raise Invalid(f"team {team_id} is retired")


def team_filter(team_id: int | None, alias: str = "a") -> tuple[str, tuple]:
    """SQL to keep only one team's assessments (nothing when no team is chosen)."""
    return (f" AND {alias}.team_id = ?", (team_id,)) if team_id is not None else ("", ())


# A skill counts for a team if the team has no categories chosen (= all), or its category is one of them.
# {team} is an SQL expression for the team id (NULL = no team = all skills), {skill} the skills table's alias.
_IN_TEAM_SQL = """(NOT EXISTS (SELECT 1 FROM team_categories tc WHERE tc.team_id = {team})
    OR {skill}.category IN (SELECT tc.category FROM team_categories tc WHERE tc.team_id = {team}))"""


def team_categories(conn: sqlite3.Connection, team_id: int) -> list[str]:
    """The categories a team uses, in display order; empty = all of them."""
    return [
        r[0]
        for r in conn.execute(
            """
        SELECT tc.category FROM team_categories tc
        LEFT JOIN (SELECT category, MIN(sort_order) AS o FROM skills GROUP BY category) s ON s.category = tc.category
        WHERE tc.team_id = ? ORDER BY s.o, tc.category""",
            (team_id,),
        )
    ]


# ---------- assessments ----------

# Rounds are ordered by date, undated rounds counting as newest, then by id: the same order as list_rounds.
# The person's latest assessment in a round ordered before round :round_id.
_PREVIOUS_SQL = """
    SELECT a.id, a.round_id, a.role_id, a.team_id, r.label FROM assessments a JOIN rounds r ON r.id = a.round_id
    WHERE a.person_id = :person_id
      AND (COALESCE(r.held_on, '9999-12-31'), r.id)
          < (SELECT COALESCE(t.held_on, '9999-12-31'), t.id FROM rounds t WHERE t.id = :round_id)
    ORDER BY COALESCE(r.held_on, '9999-12-31') DESC, r.id DESC LIMIT 1"""


def _previous(conn: sqlite3.Connection, round_id: int, person_id: int):
    return conn.execute(_PREVIOUS_SQL, {"person_id": person_id, "round_id": round_id}).fetchone()


def round_status(conn: sqlite3.Connection, round_id: int) -> list[PersonStatus]:
    get_round(conn, round_id)
    rows = conn.execute(
        """
        SELECT p.id, p.name, a.id AS aid, ro.name AS role_name, tm.id AS tid, tm.name AS tname, tm.retired_on AS tret,
               (SELECT COUNT(*) FROM ratings x WHERE x.assessment_id = a.id
                  AND (x.self IS NOT NULL OR x.agreed IS NOT NULL OR x.desire IS NOT NULL)) AS rated
        FROM people p
        LEFT JOIN assessments a ON a.person_id = p.id AND a.round_id = ?
        LEFT JOIN roles ro ON ro.id = a.role_id
        LEFT JOIN teams tm ON tm.id = a.team_id
        WHERE p.active = 1 ORDER BY p.name""",
        (round_id,),
    ).fetchall()
    out = []
    for r in rows:
        prev = _previous(conn, round_id, r["id"])
        team = Team(r["tid"], r["tname"], r["tret"]) if r["tid"] is not None else None
        out.append(
            PersonStatus(
                Person(r["id"], r["name"]),
                r["aid"],
                r["role_name"],
                r["rated"] or 0,
                prev["role_id"] if prev else None,
                prev["label"] if prev else None,
                team,
                prev["team_id"] if prev else None,
            )
        )
    return out


def get_assessment(conn: sqlite3.Connection, round_id: int, person_id: int) -> Assessment | None:
    r = conn.execute(
        """
        SELECT a.id, p.id AS pid, p.name AS pname, r.id AS rid, r.label, r.held_on, ro.id AS roid, ro.name AS roname,
               tm.id AS tid, tm.name AS tname, tm.retired_on AS tret
        FROM assessments a JOIN people p ON p.id = a.person_id JOIN rounds r ON r.id = a.round_id
        JOIN roles ro ON ro.id = a.role_id LEFT JOIN teams tm ON tm.id = a.team_id
        WHERE a.round_id = ? AND a.person_id = ?""",
        (round_id, person_id),
    ).fetchone()
    if r is None:
        return None
    team = Team(r["tid"], r["tname"], r["tret"]) if r["tid"] is not None else None
    return Assessment(
        r["id"],
        Person(r["pid"], r["pname"]),
        Round(r["rid"], r["label"], r["held_on"]),
        Role(r["roid"], r["roname"]),
        team,
    )


def previous_assessment(conn: sqlite3.Connection, a: Assessment) -> Assessment | None:
    """The same person's assessment in the latest earlier round, if any."""
    prev = _previous(conn, a.round.id, a.person.id)
    return get_assessment(conn, prev["round_id"], a.person.id) if prev else None


def start_assessment(
    conn: sqlite3.Connection, round_id: int, person_id: int, role_id: int, team_id: int | None = None
) -> Assessment:
    """Create the person's assessment for this round, with the role (and team, if any) they have at this round.

    Pre-fills from their latest earlier assessment: self = previous level used (agreed if set,
    else self), agreed = blank, desire = previous desire. Retired skills are skipped; skills that
    didn't exist then simply have nothing to copy.
    """
    get_round(conn, round_id)
    get_person(conn, person_id)
    if role_id not in {r.id for r in list_active_roles(conn)}:
        raise Invalid(f"role {role_id} is not an active role")
    _check_team(conn, team_id)
    if get_assessment(conn, round_id, person_id) is not None:
        raise Invalid("this person already has an assessment in this round")
    prev = _previous(conn, round_id, person_id)
    try:
        with conn:
            aid = conn.execute(
                "INSERT INTO assessments (person_id, round_id, role_id, team_id) VALUES (?, ?, ?, ?)",
                (person_id, round_id, role_id, team_id),
            ).lastrowid
            if prev is not None:
                conn.execute(
                    """
                    INSERT INTO ratings (assessment_id, skill_id, self, agreed, desire)
                    SELECT ?, x.skill_id, COALESCE(x.agreed, x.self), NULL, x.desire
                    FROM ratings x JOIN skills s ON s.id = x.skill_id AND s.retired_on IS NULL
                    WHERE x.assessment_id = ?
                      AND (COALESCE(x.agreed, x.self) IS NOT NULL OR x.desire IS NOT NULL)""",
                    (aid, prev["id"]),
                )
    except sqlite3.IntegrityError as e:  # started twice at once (double click): the UNIQUE constraint caught it
        if "UNIQUE" in str(e):
            raise Invalid("this person already has an assessment in this round") from e
        raise
    return get_assessment(conn, round_id, person_id)


def set_assessment_team(conn: sqlite3.Connection, round_id: int, person_id: int, team_id: int | None) -> Assessment:
    """Change the team for one round, e.g. for assessments made before teams existed. Blank = no team."""
    a = get_assessment(conn, round_id, person_id)
    if a is None:
        raise NotFound("no assessment for this person in this round")
    if team_id != (a.team.id if a.team else None):
        _check_team(conn, team_id)
    with conn:
        conn.execute("UPDATE assessments SET team_id = ? WHERE id = ?", (team_id, a.id))
    return get_assessment(conn, round_id, person_id)


# prev = the person's previous assessment (or NULL); its ratings are shown next to this round's
_ROWS_SQL = """
    SELECT s.id, s.category, s.name, s.description, s.tag, s.priority, t.level AS target,
           x.self, x.agreed, x.desire,
           :prev IS NOT NULL AS compared, COALESCE(p.agreed, p.self) AS prev_used, p.desire AS prev_desire
    FROM assessments a
    JOIN skills s ON s.retired_on IS NULL
    LEFT JOIN targets t ON t.skill_id = s.id AND t.role_id = a.role_id
    LEFT JOIN ratings x ON x.skill_id = s.id AND x.assessment_id = a.id
    LEFT JOIN ratings p ON p.skill_id = s.id AND p.assessment_id = :prev
    WHERE a.id = :aid {extra}
    ORDER BY s.sort_order"""


def _row(r) -> RatingRow:
    return RatingRow(
        r["id"],
        r["category"],
        r["name"],
        r["description"],
        r["tag"],
        r["priority"],
        r["target"],
        r["self"],
        r["agreed"],
        r["desire"],
        bool(r["compared"]),
        r["prev_used"],
        r["prev_desire"],
    )


def _params(conn: sqlite3.Connection, assessment_id: int, **extra) -> dict:
    a = conn.execute("SELECT person_id, round_id FROM assessments WHERE id = ?", (assessment_id,)).fetchone()
    if a is None:
        raise NotFound(f"no assessment {assessment_id}")
    prev = _previous(conn, a["round_id"], a["person_id"])
    return {"aid": assessment_id, "prev": prev["id"] if prev else None, **extra}


def rating_rows(
    conn: sqlite3.Connection, assessment_id: int, tag: str | None = None, team_only: bool = True
) -> list[RatingRow]:
    """Active skills with this assessment's ratings, role targets and last round's values, in display order.

    team_only: only the categories the assessment's team uses (all, if it has no team or the team chose none).
    """
    extra = "AND s.tag = :tag" if tag else ""
    if team_only:
        extra += " AND " + _IN_TEAM_SQL.format(team="a.team_id", skill="s")
    return [_row(r) for r in conn.execute(_ROWS_SQL.format(extra=extra), _params(conn, assessment_id, tag=tag))]


def save_rating(
    conn: sqlite3.Connection,
    assessment_id: int,
    skill_id: int,
    self_level: int | None,
    agreed: int | None,
    desire: int | None,
) -> RatingRow:
    """Store one skill's ratings. All three blank removes the row. Retired skills can't be rated."""
    for value, what in ((self_level, "self"), (agreed, "agreed")):
        _check_level(value, what)
    _check_desire(desire)
    params = _params(conn, assessment_id, sid=skill_id)
    if conn.execute("SELECT 1 FROM skills WHERE id = ? AND retired_on IS NULL", (skill_id,)).fetchone() is None:
        raise NotFound(f"no active skill {skill_id}")
    with conn:
        if self_level is None and agreed is None and desire is None:
            conn.execute("DELETE FROM ratings WHERE assessment_id = ? AND skill_id = ?", (assessment_id, skill_id))
        else:
            conn.execute(
                """
                INSERT INTO ratings (assessment_id, skill_id, self, agreed, desire) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (assessment_id, skill_id)
                DO UPDATE SET self = excluded.self, agreed = excluded.agreed, desire = excluded.desire""",
                (assessment_id, skill_id, self_level, agreed, desire),
            )
    return _row(conn.execute(_ROWS_SQL.format(extra="AND s.id = :sid"), params).fetchone())


# ---------- overview and profile ----------


def previous_round(conn: sqlite3.Connection, round_id: int) -> Round | None:
    """The round ordered just before this one (same order as list_rounds)."""
    r = conn.execute(
        """
        SELECT * FROM rounds r
        WHERE (COALESCE(r.held_on, '9999-12-31'), r.id)
              < (SELECT COALESCE(t.held_on, '9999-12-31'), t.id FROM rounds t WHERE t.id = ?)
        ORDER BY COALESCE(r.held_on, '9999-12-31') DESC, r.id DESC LIMIT 1""",
        (round_id,),
    ).fetchone()
    return Round(r["id"], r["label"], r["held_on"]) if r else None


def tags(conn: sqlite3.Connection, include_retired: bool = False) -> list[str]:
    """Tags in use (blank = untagged, never listed), in the order they first appear in the catalogue."""
    where = "" if include_retired else "AND retired_on IS NULL"
    return [
        r[0]
        for r in conn.execute(f"SELECT tag FROM skills WHERE tag != '' {where} GROUP BY tag ORDER BY MIN(sort_order)")
    ]


def _active_skills(conn: sqlite3.Connection, tag: str | None, team_id: int | None = None):
    """Active skills (with the tag, and in the team's categories, if given) in display order."""
    sql, args = "SELECT id, category, name, tag, priority, experts_needed FROM skills WHERE retired_on IS NULL", []
    if tag:
        sql += " AND tag = ?"
        args.append(tag)
    if team_id is not None:
        sql += " AND " + _IN_TEAM_SQL.format(team="?", skill="skills")
        args += [team_id, team_id]
    return conn.execute(sql + " ORDER BY sort_order", args).fetchall()


def _round_stats(
    conn: sqlite3.Connection, round_id: int, skills, team_id: int | None = None
) -> tuple[list[metrics.SkillStats], metrics.Headline]:
    """Per-skill stats (in the order of `skills`) and the headline figures for one round (one team's, if given)."""
    wanted = {s["id"] for s in skills}
    only, args = team_filter(team_id)
    by_person: dict[int, list[metrics.Observation]] = {
        r[0]: []
        for r in conn.execute("SELECT a.person_id FROM assessments a WHERE a.round_id = ?" + only, (round_id, *args))
    }
    by_skill: dict[int, list[metrics.Observation]] = {sid: [] for sid in wanted}
    for r in conn.execute(
        """
            SELECT a.person_id, x.skill_id, x.self, x.agreed, x.desire, t.level AS target
            FROM assessments a JOIN ratings x ON x.assessment_id = a.id
            LEFT JOIN targets t ON t.role_id = a.role_id AND t.skill_id = x.skill_id
            WHERE a.round_id = ?"""
        + only,
        (round_id, *args),
    ):
        if r["skill_id"] not in wanted:
            continue
        o = metrics.Observation(metrics.level_used(r["self"], r["agreed"]), r["desire"], r["target"])
        by_person[r["person_id"]].append(o)
        by_skill[r["skill_id"]].append(o)
    stats = [metrics.skill_stats(by_skill[s["id"]], s["priority"], s["experts_needed"]) for s in skills]
    return stats, metrics.headline(by_person, stats)


def team_overview(
    conn: sqlite3.Connection, round_id: int, tag: str | None = None, team_id: int | None = None
) -> TeamOverview:
    """Per-skill team figures, category roll-up and headline figures, compared with the previous round.

    Both rounds are measured over today's active skills and today's targets (targets have no history).
    With a team, each round counts the people who were in that team then; open goals are those of the
    people in the team this round.
    """
    rnd = get_round(conn, round_id)
    skills = _active_skills(conn, tag, team_id)
    stats, head = _round_stats(conn, round_id, skills, team_id)
    prev = previous_round(conn, round_id)
    prev_head = _round_stats(conn, prev.id, skills, team_id)[1] if prev else None
    rows = [
        SkillOverview(s["id"], s["category"], s["name"], s["tag"], s["priority"], s["experts_needed"], st)
        for s, st in zip(skills, stats)
    ]
    sql, args = "SELECT COUNT(*) FROM goals g WHERE g.status IN ('open', 'in_progress')", ()
    if team_id is not None:
        sql += " AND g.person_id IN (SELECT a.person_id FROM assessments a WHERE a.round_id = ? AND a.team_id = ?)"
        args = (round_id, team_id)
    open_goals = conn.execute(sql, args).fetchone()[0]
    return TeamOverview(
        rnd, prev, rows, metrics.category_stats([(r.category, r.stats) for r in rows]), head, prev_head, open_goals
    )


def person_profile(conn: sqlite3.Connection, round_id: int, person_id: int, tag: str | None = None) -> PersonProfile:
    a = get_assessment(conn, round_id, person_id)
    if a is None:
        raise NotFound("no assessment for this person in this round")
    rows = rating_rows(conn, a.id, tag)
    return PersonProfile(
        a,
        previous_assessment(conn, a),
        rows,
        metrics.person_summary(rows),
        metrics.top_gaps(rows),
        metrics.top_interests(rows),
        metrics.changes_since_last_round(rows),
    )


def heatmap(conn: sqlite3.Connection, round_id: int, tag: str | None = None, team_id: int | None = None) -> Heatmap:
    """Every assessed person (in the team, if given) x every active skill for one round."""
    rnd = get_round(conn, round_id)
    skills = _active_skills(conn, tag, team_id)
    only, args = team_filter(team_id)
    people = conn.execute(
        """
        SELECT a.person_id FROM assessments a JOIN people p ON p.id = a.person_id
        WHERE a.round_id = ?"""
        + only
        + " ORDER BY p.name",
        (round_id, *args),
    ).fetchall()
    columns = [get_assessment(conn, round_id, r["person_id"]) for r in people]
    col = {a.id: i for i, a in enumerate(columns)}
    blank = {s["id"]: [HeatCell(None, None, None) for _ in columns] for s in skills}
    for r in conn.execute(
        """
            SELECT a.id AS aid, s.id AS skill_id, x.self, x.agreed, x.desire, t.level AS target
            FROM assessments a
            JOIN skills s ON s.retired_on IS NULL
            LEFT JOIN ratings x ON x.assessment_id = a.id AND x.skill_id = s.id
            LEFT JOIN targets t ON t.role_id = a.role_id AND t.skill_id = s.id
            WHERE a.round_id = ?"""
        + only,
        (round_id, *args),
    ):
        if r["skill_id"] in blank:
            blank[r["skill_id"]][col[r["aid"]]] = HeatCell(
                metrics.level_used(r["self"], r["agreed"]), r["desire"], r["target"]
            )
    rows = [HeatRow(s["id"], s["category"], s["name"], s["tag"], blank[s["id"]]) for s in skills]
    return Heatmap(rnd, columns, rows)


# ---------- development goals ----------


def _round_order(conn: sqlite3.Connection, round_id: int) -> tuple[str, int]:
    r = conn.execute("SELECT COALESCE(held_on, '9999-12-31'), id FROM rounds WHERE id = ?", (round_id,)).fetchone()
    if r is None:
        raise NotFound(f"no round {round_id}")
    return tuple(r)


_GOAL_SQL = """
    SELECT g.*, s.name AS skill_name,
           cr.label AS c_label, cr.held_on AS c_held, dr.label AS d_label, dr.held_on AS d_held
    FROM goals g JOIN rounds cr ON cr.id = g.created_round_id
    LEFT JOIN rounds dr ON dr.id = g.due_round_id
    LEFT JOIN skills s ON s.id = g.skill_id"""


def _goal(r) -> Goal:
    due = Round(r["due_round_id"], r["d_label"], r["d_held"]) if r["due_round_id"] else None
    return Goal(
        r["id"],
        r["person_id"],
        r["skill_id"],
        r["skill_name"],
        r["title"],
        r["target_level"],
        Round(r["created_round_id"], r["c_label"], r["c_held"]),
        due,
        r["status"],
        r["actions"],
        r["notes"],
    )


def get_goal(conn: sqlite3.Connection, goal_id: int) -> Goal:
    r = conn.execute(_GOAL_SQL + " WHERE g.id = ?", (goal_id,)).fetchone()
    if r is None:
        raise NotFound(f"no goal {goal_id}")
    return _goal(r)


def _used(conn: sqlite3.Connection, assessment_id: int | None, skill_id: int | None) -> int | None:
    if assessment_id is None or skill_id is None:
        return None
    r = conn.execute(
        "SELECT COALESCE(agreed, self) FROM ratings WHERE assessment_id = ? AND skill_id = ?", (assessment_id, skill_id)
    ).fetchone()
    return r[0] if r else None


def list_goals(conn: sqlite3.Connection, person_id: int, round_id: int) -> list[GoalProgress]:
    """The person's goals created in or before this round, with progress as of this round."""
    get_person(conn, person_id)
    here = _round_order(conn, round_id)
    a = get_assessment(conn, round_id, person_id)
    prev = _previous(conn, round_id, person_id)
    prev_round = get_round(conn, prev["round_id"]) if prev else None
    out = []
    for r in conn.execute(_GOAL_SQL + " WHERE g.person_id = ? ORDER BY g.id", (person_id,)):
        g = _goal(r)
        if _round_order(conn, g.created_round.id) > here:
            continue
        out.append(
            GoalProgress(
                g,
                _used(conn, a.id if a else None, g.skill_id),
                _used(conn, prev["id"] if prev else None, g.skill_id),
                prev_round,
            )
        )
    order = {s: i for i, s in enumerate(GOAL_STATUSES)}
    return sorted(out, key=lambda p: order[p.goal.status])


def _check_goal(
    conn: sqlite3.Connection,
    title: str,
    target_level: int | None,
    created_round_id: int,
    due_round_id: int | None,
    status: str,
) -> str:
    title = title.strip()
    if not title:
        raise Invalid("goal title is required")
    if len(title) > 200:
        raise Invalid("goal title is too long (200 characters at most)")
    _check_level(target_level, "target level")
    if status not in GOAL_STATUSES:
        raise Invalid(f"status must be one of {', '.join(GOAL_STATUSES)}")
    if due_round_id is not None and _round_order(conn, due_round_id) < _round_order(conn, created_round_id):
        raise Invalid("the due round can't be before the round the goal was set in")
    return title


def create_goal(
    conn: sqlite3.Connection,
    person_id: int,
    created_round_id: int,
    title: str,
    skill_id: int | None = None,
    target_level: int | None = None,
    due_round_id: int | None = None,
    actions: str = "",
    notes: str = "",
) -> Goal:
    get_person(conn, person_id)
    title = _check_goal(conn, title, target_level, created_round_id, due_round_id, "open")
    if (
        skill_id is not None
        and conn.execute("SELECT 1 FROM skills WHERE id = ? AND retired_on IS NULL", (skill_id,)).fetchone() is None
    ):
        raise Invalid("goals can only be set on active skills")
    with conn:
        gid = conn.execute(
            "INSERT INTO goals (person_id, skill_id, title, target_level, created_round_id, due_round_id, actions, notes)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (person_id, skill_id, title, target_level, created_round_id, due_round_id, actions.strip(), notes.strip()),
        ).lastrowid
    return get_goal(conn, gid)


def update_goal(
    conn: sqlite3.Connection,
    goal_id: int,
    title: str,
    target_level: int | None,
    due_round_id: int | None,
    status: str,
    actions: str,
    notes: str,
) -> Goal:
    """Edit everything except the person, the skill and the round it was set in."""
    g = get_goal(conn, goal_id)
    title = _check_goal(conn, title, target_level, g.created_round.id, due_round_id, status)
    with conn:
        conn.execute(
            "UPDATE goals SET title = ?, target_level = ?, due_round_id = ?, status = ?, actions = ?, notes = ?"
            " WHERE id = ?",
            (title, target_level, due_round_id, status, actions.strip(), notes.strip(), goal_id),
        )
    return get_goal(conn, goal_id)


def set_goal_status(conn: sqlite3.Connection, goal_id: int, status: str) -> Goal:
    g = get_goal(conn, goal_id)
    if status not in GOAL_STATUSES:
        raise Invalid(f"status must be one of {', '.join(GOAL_STATUSES)}")
    with conn:
        conn.execute("UPDATE goals SET status = ? WHERE id = ?", (status, g.id))
    return get_goal(conn, goal_id)


# ---------- comparing rounds ----------

TOP_CHANGES = 10


def _oldest_first(conn: sqlite3.Connection) -> list[Round]:
    return list_rounds(conn)[::-1]


def _retired_between(
    conn: sqlite3.Connection, before: Round, after: Round, tag: str | None, team_id: int | None = None
):
    """Skills retired after the earlier round's date, up to the later one's. Needs both dates."""
    if before.held_on is None or after.held_on is None:
        return []
    lo, hi = sorted((before.held_on, after.held_on))
    sql = "SELECT name, retired_on FROM skills WHERE retired_on > ? AND retired_on <= ?"
    args = [lo, hi]
    if tag:
        sql += " AND tag = ?"
        args.append(tag)
    if team_id is not None:
        sql += " AND " + _IN_TEAM_SQL.format(team="?", skill="skills")
        args += [team_id, team_id]
    return [(r[0], r[1]) for r in conn.execute(sql + " ORDER BY sort_order", args)]


def compare_team(
    conn: sqlite3.Connection, before_id: int, after_id: int, tag: str | None = None, team_id: int | None = None
) -> TeamComparison:
    """Team figures for two rounds over today's active skills and targets, with trends over all rounds.

    With a team, each round counts the people who were in that team then.
    """
    if before_id == after_id:
        raise Invalid("choose two different rounds")
    before_round, after_round = get_round(conn, before_id), get_round(conn, after_id)
    skills = _active_skills(conn, tag, team_id)
    rounds = _oldest_first(conn)
    per_round = {r.id: _round_stats(conn, r.id, skills, team_id) for r in rounds}
    (b_stats, b_head), (a_stats, a_head) = per_round[before_id], per_round[after_id]
    changed = _changed_skills(conn, before_id, after_id, team_id)
    rows = [
        SkillComparison(
            s["id"],
            s["category"],
            s["name"],
            s["tag"],
            b,
            a,
            [per_round[r.id][0][i].median_level for r in rounds],
            s["id"] in changed,
        )
        for i, (s, b, a) in enumerate(zip(skills, b_stats, a_stats))
    ]
    moved = [r for r in rows if r.level_delta]
    improvements = sorted((r for r in moved if r.level_delta > 0), key=lambda r: -r.level_delta)[:TOP_CHANGES]
    declines = sorted((r for r in moved if r.level_delta < 0), key=lambda r: r.level_delta)[:TOP_CHANGES]
    first_rated = [r for r in rows if r.before.assessed == 0 and r.after.assessed > 0]
    return TeamComparison(
        before_round,
        after_round,
        rounds,
        rows,
        b_head,
        a_head,
        improvements,
        declines,
        first_rated,
        _retired_between(conn, before_round, after_round, tag, team_id),
    )


def _changed_skills(conn: sqlite3.Connection, before_id: int, after_id: int, team_id: int | None) -> set[int]:
    """Skills where someone assessed in both rounds has a different level used or desire, as in the person view.

    Going from unrated to rated counts as a change; people in only one of the rounds (joiners, leavers) don't.
    """
    only, args = team_filter(team_id)
    values: dict[int, dict[int, dict[int, tuple]]] = {before_id: {}, after_id: {}}
    for round_id, people in values.items():
        for r in conn.execute("SELECT a.person_id FROM assessments a WHERE a.round_id = ?" + only, (round_id, *args)):
            people[r["person_id"]] = {}
        for r in conn.execute(
            """
                SELECT a.person_id, x.skill_id, COALESCE(x.agreed, x.self) AS used, x.desire
                FROM assessments a JOIN ratings x ON x.assessment_id = a.id WHERE a.round_id = ?"""
            + only,
            (round_id, *args),
        ):
            people[r["person_id"]][r["skill_id"]] = (r["used"], r["desire"])
    both = values[before_id].keys() & values[after_id].keys()
    empty = (None, None)
    return {
        sid
        for p in both
        for sid in values[before_id][p].keys() | values[after_id][p].keys()
        if values[before_id][p].get(sid, empty) != values[after_id][p].get(sid, empty)
    }


def compare_person(
    conn: sqlite3.Connection, before_id: int, after_id: int, person_id: int, tag: str | None = None
) -> PersonComparison:
    if before_id == after_id:
        raise Invalid("choose two different rounds")
    person = get_person(conn, person_id)
    before_round, after_round = get_round(conn, before_id), get_round(conn, after_id)
    skills = _active_skills(conn, tag)
    rounds = _oldest_first(conn)
    values: dict[tuple[int, int], tuple[int | None, int | None]] = {}
    for r in conn.execute(
        """
            SELECT a.round_id, x.skill_id, COALESCE(x.agreed, x.self) AS used, x.desire
            FROM assessments a JOIN ratings x ON x.assessment_id = a.id WHERE a.person_id = ?""",
        (person_id,),
    ):
        values[(r["round_id"], r["skill_id"])] = (r["used"], r["desire"])
    before, after = get_assessment(conn, before_id, person_id), get_assessment(conn, after_id, person_id)
    targets = {}
    if after is not None:
        targets = dict(
            conn.execute("SELECT skill_id, level FROM targets WHERE role_id = ?", (after.role.id,)).fetchall()
        )
    empty = (None, None)
    rows = []
    for s in skills:
        b, a = values.get((before_id, s["id"]), empty), values.get((after_id, s["id"]), empty)
        rows.append(
            PersonSkillComparison(
                s["id"],
                s["category"],
                s["name"],
                s["tag"],
                targets.get(s["id"]),
                b[0],
                a[0],
                b[1],
                a[1],
                [values.get((r.id, s["id"]), empty)[0] for r in rounds],
            )
        )
    moved = [r for r in rows if r.level_delta]
    improvements = sorted((r for r in moved if r.level_delta > 0), key=lambda r: -r.level_delta)[:TOP_CHANGES]
    declines = sorted((r for r in moved if r.level_delta < 0), key=lambda r: r.level_delta)[:TOP_CHANGES]
    return PersonComparison(person, before_round, after_round, before, after, rounds, rows, improvements, declines)
