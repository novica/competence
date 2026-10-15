"""What the front page shows (the newest round's progress, the team's state, what needs attention), plus the
rounds and people lists.

Only puts existing figures together (team overview, round status, certifications); it calculates nothing new.
"""

import sqlite3
from collections import Counter
from dataclasses import dataclass

from competence.core import certifications, services
from competence.core.models import Person, Round, TeamOverview


@dataclass(frozen=True)
class Progress:
    round: Round
    assessed: int  # at least one skill rated
    started: int  # assessment started, nothing rated yet
    not_started: int
    people: int

    @property
    def complete(self) -> bool:
        return self.people > 0 and self.assessed == self.people


@dataclass(frozen=True)
class PersonRow:
    """An active person and their newest assessment, for the people page."""

    person: Person
    latest_round: Round | None  # None = never assessed
    role: str | None  # role and team in that assessment
    team: str | None
    open_goals: int  # open or in progress
    cert_attention: int  # certifications missing, expired or expiring, as of today


@dataclass(frozen=True)
class Dashboard:
    rounds: list[Round]  # newest first
    progress: Progress | None  # the newest round
    overview: TeamOverview | None  # the newest round that has assessments
    flagged: int  # skills with a flag in that overview
    cert_attention: int  # certifications missing, expired or expiring, as of today
    certs: int  # active certifications
    people: int  # active people (in the team, if given)
    skills: int  # active skills

    @property
    def getting_started(self) -> bool:
        return not self.skills or not self.people or not self.rounds


def progress(conn: sqlite3.Connection, rnd: Round) -> Progress:
    status = services.round_status(conn, rnd.id)
    assessed = sum(s.assessment_id is not None and s.rated > 0 for s in status)
    started = sum(s.assessment_id is not None and s.rated == 0 for s in status)
    return Progress(rnd, assessed, started, len(status) - assessed - started, len(status))


def dashboard(conn: sqlite3.Connection, team_id: int | None = None) -> Dashboard:
    """With a team: its state, certifications and people; the newest round's progress stays the whole round's."""
    rounds = services.list_rounds(conn)
    newest = progress(conn, rounds[0]) if rounds else None
    in_team, args = services.team_filter(team_id)
    assessed_round = next(
        (
            r
            for r in rounds
            if conn.execute("SELECT 1 FROM assessments a WHERE a.round_id = ?" + in_team, (r.id, *args)).fetchone()
        ),
        None,
    )
    overview = services.team_overview(conn, assessed_round.id, team_id=team_id) if assessed_round else None
    team_certs = certifications.team_certifications(conn, team_id=team_id)
    return Dashboard(
        rounds=rounds,
        progress=newest,
        overview=overview,
        flagged=sum(s.stats.flag is not None for s in overview.skills) if overview else 0,
        cert_attention=len(team_certs.attention),
        certs=len(team_certs.certs),
        people=len(_people(conn, team_id)),
        skills=conn.execute("SELECT COUNT(*) FROM skills WHERE retired_on IS NULL").fetchone()[0],
    )


def rounds_progress(conn: sqlite3.Connection) -> list[Progress]:
    """Every round with its progress, newest first."""
    return [progress(conn, r) for r in services.list_rounds(conn)]


def _people(conn: sqlite3.Connection, team_id: int | None) -> list[sqlite3.Row]:
    """Active people by name with their newest assessment; with a team, those whose newest assessment is in it
    (as certifications.current_team_id decides)."""
    rows = conn.execute("""
        WITH latest AS (
            SELECT p.id, p.name, (SELECT a.id FROM assessments a JOIN rounds r ON r.id = a.round_id
                                  WHERE a.person_id = p.id
                                  ORDER BY COALESCE(r.held_on, '9999-12-31') DESC, r.id DESC LIMIT 1) AS assessment_id
            FROM people p WHERE p.active = 1)
        SELECT l.id, l.name, r.id AS round_id, r.label, r.held_on, ro.name AS role, a.team_id, t.name AS team,
               (SELECT COUNT(*) FROM goals g WHERE g.person_id = l.id
                AND g.status IN ('open', 'in_progress')) AS open_goals
        FROM latest l LEFT JOIN assessments a ON a.id = l.assessment_id LEFT JOIN rounds r ON r.id = a.round_id
             LEFT JOIN roles ro ON ro.id = a.role_id LEFT JOIN teams t ON t.id = a.team_id
        ORDER BY l.name""").fetchall()
    return [r for r in rows if team_id is None or r["team_id"] == team_id]


def people(conn: sqlite3.Connection, team_id: int | None = None) -> list[PersonRow]:
    """The people page: each active person's newest assessment, open goals and certifications needing attention."""
    attention = Counter(a.person.id for a in certifications.team_certifications(conn, team_id=team_id).attention)
    return [
        PersonRow(
            Person(r["id"], r["name"]),
            Round(r["round_id"], r["label"], r["held_on"]) if r["round_id"] else None,
            r["role"],
            r["team"],
            r["open_goals"],
            attention[r["id"]],
        )
        for r in _people(conn, team_id)
    ]
