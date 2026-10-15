"""Certifications: the catalogue (what exists, how long it lasts, which roles need it), who holds what, and status.

Status is worked out on the day it's shown, from the newest record per person and certification:
- valid:    held, and doesn't expire within EXPIRING_DAYS
- expiring: held, expires within EXPIRING_DAYS
- expired:  the newest record has expired
- missing:  the person's current role requires it and they have never held it
A person's current role is the role of their newest assessment (same round order as everywhere else).
Like skills, a certification with records is retired, never deleted.
"""

import calendar
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from competence.core.models import Person, Role
from competence.core.services import Invalid, NotFound, get_person

EXPIRING_DAYS = 60
STATUSES = ("missing", "expired", "expiring", "valid")  # most urgent first
STATUS_LABELS = {"missing": "Missing", "expired": "Expired", "expiring": "Expiring soon", "valid": "Valid"}
STATUS_RULES = {  # shown in the in-app guide
    "missing": "The person's role requires it and they have never held it.",
    "expired": "The newest record's expiry date has passed.",
    "expiring": f"Held, but expires within the next {EXPIRING_DAYS} days.",
    "valid": f"Held, and doesn't expire within the next {EXPIRING_DAYS} days (or never expires).",
}


@dataclass(frozen=True)
class CertInfo:
    id: int
    name: str
    description: str
    valid_months: int | None  # None = never expires
    retired_on: str | None
    required_by: tuple[int, ...]  # role ids
    records: int  # delete is only allowed at 0


@dataclass(frozen=True)
class Record:
    id: int
    person_id: int
    certification_id: int
    obtained_on: str
    expires_on: str | None
    notes: str


@dataclass(frozen=True)
class PersonCert:
    cert: CertInfo
    required: bool
    current: Record | None  # the newest record
    status: str | None  # None = not held and not required
    history: list[Record]  # newest first


@dataclass(frozen=True)
class PersonCerts:
    person: Person
    role: Role | None  # from the newest assessment; None = never assessed, so nothing is required
    rows: list[PersonCert]  # active certifications, plus retired ones this person has records for


@dataclass(frozen=True)
class CertSummary:
    cert: CertInfo
    counts: dict[str, int]  # status -> number of active people
    nobody_holds_it: bool  # no active person has a valid (or expiring) one


@dataclass(frozen=True)
class Attention:
    person: Person
    cert: CertInfo
    status: str  # missing, expired or expiring
    record: Record | None


@dataclass(frozen=True)
class TeamCerts:
    today: str
    certs: list[CertSummary]
    people: list[Person]
    cells: dict[tuple[int, int], tuple[str | None, Record | None]]  # (person_id, cert_id) -> (status, current)
    attention: list[Attention]  # most urgent first


# ---------- dates and status ----------


def add_months(d: date, months: int) -> date:
    """Same day of the month, months later; the 31st becomes the month's last day where needed."""
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def status_of(current: Record | None, required: bool, today: date) -> str | None:
    if current is None:
        return "missing" if required else None
    if current.expires_on is None:
        return "valid"
    expires = date.fromisoformat(current.expires_on)
    if expires < today:
        return "expired"
    return "expiring" if expires <= today + timedelta(days=EXPIRING_DAYS) else "valid"


def current_role(conn: sqlite3.Connection, person_id: int) -> Role | None:
    r = conn.execute(
        """
        SELECT ro.id, ro.name FROM assessments a JOIN rounds r ON r.id = a.round_id JOIN roles ro ON ro.id = a.role_id
        WHERE a.person_id = ? ORDER BY COALESCE(r.held_on, '9999-12-31') DESC, r.id DESC LIMIT 1""",
        (person_id,),
    ).fetchone()
    return Role(r["id"], r["name"]) if r else None


# ---------- catalogue ----------


def list_certifications(conn: sqlite3.Connection, include_retired: bool = True) -> list[CertInfo]:
    required: dict[int, list[int]] = {}
    for r in conn.execute("SELECT certification_id, role_id FROM role_certifications ORDER BY role_id"):
        required.setdefault(r[0], []).append(r[1])
    sql = """SELECT c.*, (SELECT COUNT(*) FROM person_certifications p WHERE p.certification_id = c.id) AS n
             FROM certifications c"""
    if not include_retired:
        sql += " WHERE c.retired_on IS NULL"
    return [
        CertInfo(
            r["id"],
            r["name"],
            r["description"],
            r["valid_months"],
            r["retired_on"],
            tuple(required.get(r["id"], ())),
            r["n"],
        )
        for r in conn.execute(sql + " ORDER BY c.name COLLATE NOCASE")
    ]


def get_certification(conn: sqlite3.Connection, cert_id: int) -> CertInfo:
    for c in list_certifications(conn):
        if c.id == cert_id:
            return c
    raise NotFound(f"no certification {cert_id}")


def _clean(conn, name: str, description: str, valid_months: int | None, role_ids: list[int]) -> tuple[str, str]:
    name, description = name.strip(), description.strip()
    if not name:
        raise Invalid("name is required")
    if valid_months is not None and not 1 <= valid_months <= 600:
        raise Invalid("valid for must be between 1 and 600 months, or blank for no expiry")
    known = {r[0] for r in conn.execute("SELECT id FROM roles")}
    if unknown := set(role_ids) - known:
        raise Invalid(f"unknown role {min(unknown)}")
    return name, description


def _set_roles(conn, cert_id: int, role_ids: list[int]) -> None:
    conn.execute("DELETE FROM role_certifications WHERE certification_id = ?", (cert_id,))
    conn.executemany(
        "INSERT INTO role_certifications (role_id, certification_id) VALUES (?, ?)",
        [(rid, cert_id) for rid in sorted(set(role_ids))],
    )


def add_certification(
    conn: sqlite3.Connection, name: str, description: str, valid_months: int | None, role_ids: list[int]
) -> int:
    name, description = _clean(conn, name, description, valid_months, role_ids)
    try:
        with conn:
            cid = conn.execute(
                "INSERT INTO certifications (name, description, valid_months) VALUES (?, ?, ?)",
                (name, description, valid_months),
            ).lastrowid
            _set_roles(conn, cid, role_ids)
    except sqlite3.IntegrityError as e:
        raise Invalid(f"there is already a certification called {name!r}") from e
    return cid


def update_certification(
    conn: sqlite3.Connection, cert_id: int, name: str, description: str, valid_months: int | None, role_ids: list[int]
) -> None:
    """Changing how long it lasts only affects records added from now on; existing expiry dates stay."""
    get_certification(conn, cert_id)
    name, description = _clean(conn, name, description, valid_months, role_ids)
    try:
        with conn:
            conn.execute(
                "UPDATE certifications SET name = ?, description = ?, valid_months = ? WHERE id = ?",
                (name, description, valid_months, cert_id),
            )
            _set_roles(conn, cert_id, role_ids)
    except sqlite3.IntegrityError as e:
        raise Invalid(f"there is already a certification called {name!r}") from e


def retire_certification(conn: sqlite3.Connection, cert_id: int, on: date | None = None) -> None:
    get_certification(conn, cert_id)
    with conn:
        conn.execute(
            "UPDATE certifications SET retired_on = ? WHERE id = ?", ((on or date.today()).isoformat(), cert_id)
        )


def unretire_certification(conn: sqlite3.Connection, cert_id: int) -> None:
    get_certification(conn, cert_id)
    with conn:
        conn.execute("UPDATE certifications SET retired_on = NULL WHERE id = ?", (cert_id,))


def delete_certification(conn: sqlite3.Connection, cert_id: int) -> None:
    """Only for mistakes: a certification nobody holds or has held. Otherwise retire it."""
    c = get_certification(conn, cert_id)
    if c.records:
        raise Invalid(f"{c.name!r} has records; retire it instead of deleting")
    with conn:
        conn.execute("DELETE FROM role_certifications WHERE certification_id = ?", (cert_id,))
        conn.execute("DELETE FROM certifications WHERE id = ?", (cert_id,))


# ---------- records ----------


def _record(r) -> Record:
    return Record(r["id"], r["person_id"], r["certification_id"], r["obtained_on"], r["expires_on"], r["notes"])


def add_record(
    conn: sqlite3.Connection,
    person_id: int,
    cert_id: int,
    obtained_on: date,
    expires_on: date | None = None,
    notes: str = "",
    today: date | None = None,
) -> int:
    """Someone obtained or renewed a certification. Blank expiry = worked out from how long it lasts."""
    get_person(conn, person_id)
    c = get_certification(conn, cert_id)
    if c.retired_on:
        raise Invalid(f"{c.name!r} is retired")
    if obtained_on > (today or date.today()):
        raise Invalid("the date obtained can't be in the future")
    if expires_on is None and c.valid_months is not None:
        expires_on = add_months(obtained_on, c.valid_months)
    if expires_on is not None and expires_on < obtained_on:
        raise Invalid("it can't expire before it was obtained")
    try:
        with conn:
            return conn.execute(
                "INSERT INTO person_certifications (person_id, certification_id, obtained_on, expires_on, notes)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    person_id,
                    cert_id,
                    obtained_on.isoformat(),
                    expires_on.isoformat() if expires_on else None,
                    notes.strip(),
                ),
            ).lastrowid
    except sqlite3.IntegrityError as e:
        raise Invalid(f"{c.name!r} is already recorded as obtained on {obtained_on.isoformat()}") from e


def delete_record(conn: sqlite3.Connection, record_id: int) -> Record:
    """For a record entered by mistake. Returns it, so the caller knows whose page to go back to."""
    r = conn.execute("SELECT * FROM person_certifications WHERE id = ?", (record_id,)).fetchone()
    if r is None:
        raise NotFound(f"no certification record {record_id}")
    with conn:
        conn.execute("DELETE FROM person_certifications WHERE id = ?", (record_id,))
    return _record(r)


def _records(conn, person_ids: list[int]) -> dict[tuple[int, int], list[Record]]:
    """(person_id, cert_id) -> records, newest first."""
    out: dict[tuple[int, int], list[Record]] = {}
    if not person_ids:
        return out
    marks = ",".join("?" * len(person_ids))
    for r in conn.execute(
        f"SELECT * FROM person_certifications WHERE person_id IN ({marks}) ORDER BY obtained_on DESC, id DESC",
        person_ids,
    ):
        out.setdefault((r["person_id"], r["certification_id"]), []).append(_record(r))
    return out


# ---------- views ----------


def person_certifications(conn: sqlite3.Connection, person_id: int, today: date | None = None) -> PersonCerts:
    today = today or date.today()
    person = get_person(conn, person_id)
    role = current_role(conn, person_id)
    records = _records(conn, [person_id])
    rows = []
    for c in list_certifications(conn):
        history = records.get((person_id, c.id), [])
        if c.retired_on and not history:
            continue
        required = role is not None and role.id in c.required_by and not c.retired_on
        current = history[0] if history else None
        rows.append(PersonCert(c, required, current, status_of(current, required, today), history))
    return PersonCerts(person, role, rows)


def current_team_id(conn: sqlite3.Connection, person_id: int) -> int | None:
    """The team in the person's newest assessment (None = no team, or never assessed)."""
    r = conn.execute(
        """
        SELECT a.team_id FROM assessments a JOIN rounds r ON r.id = a.round_id
        WHERE a.person_id = ? ORDER BY COALESCE(r.held_on, '9999-12-31') DESC, r.id DESC LIMIT 1""",
        (person_id,),
    ).fetchone()
    return r[0] if r else None


def team_certifications(conn: sqlite3.Connection, today: date | None = None, team_id: int | None = None) -> TeamCerts:
    """Active people (only those currently in the team, if given) x active certifications, as of today."""
    today = today or date.today()
    people = [Person(r["id"], r["name"]) for r in conn.execute("SELECT * FROM people WHERE active = 1 ORDER BY name")]
    if team_id is not None:
        people = [p for p in people if current_team_id(conn, p.id) == team_id]
    roles = {p.id: current_role(conn, p.id) for p in people}
    certs = list_certifications(conn, include_retired=False)
    records = _records(conn, [p.id for p in people])
    cells, attention, summaries = {}, [], []
    for c in certs:
        counts = dict.fromkeys(STATUSES, 0)
        for p in people:
            history = records.get((p.id, c.id), [])
            current = history[0] if history else None
            required = roles[p.id] is not None and roles[p.id].id in c.required_by
            status = status_of(current, required, today)
            cells[(p.id, c.id)] = (status, current)
            if status:
                counts[status] += 1
            if status in ("missing", "expired", "expiring"):
                attention.append(Attention(p, c, status, current))
        summaries.append(CertSummary(c, counts, counts["valid"] + counts["expiring"] == 0))
    attention.sort(
        key=lambda a: (
            STATUSES.index(a.status),
            a.record.expires_on if a.record else "",
            a.person.name.lower(),
            a.cert.name.lower(),
        )
    )
    return TeamCerts(today.isoformat(), summaries, people, cells, attention)
