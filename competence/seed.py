"""Create a new skills-matrix database from a catalogue file, or a demo database with invented people.

Used by `competence init`. Never overwrites: if the database file already exists, it stops.
"""

import random
import sqlite3
from pathlib import Path

from competence.core import catalogue as cat
from competence.core.db import connect, migrate


def load_catalogue(conn: sqlite3.Connection, catalogue: cat.Catalogue) -> None:
    """Insert the catalogue's roles, skills, targets, certifications and any level texts it sets.

    Priority and experts needed follow from the targets unless the catalogue sets them; level texts it leaves out
    keep the defaults.
    """
    with conn:
        role_ids = {
            name: conn.execute("INSERT INTO roles (name, sort_order) VALUES (?, ?)", (name, order)).lastrowid
            for order, name in enumerate(catalogue.roles)
        }
        for order, s in enumerate(catalogue.skills):
            skill_id = conn.execute(
                "INSERT INTO skills (category, name, description, tag, priority, experts_needed, sort_order)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (s.category, s.name, s.description, s.tag, s.effective_priority, s.effective_experts, order),
            ).lastrowid
            conn.executemany(
                "INSERT INTO targets (role_id, skill_id, level) VALUES (?, ?, ?)",
                [(role_ids[role], skill_id, t) for role, t in s.targets.items()],
            )
        for c in catalogue.certifications:
            cert_id = conn.execute(
                "INSERT INTO certifications (name, description, valid_months) VALUES (?, ?, ?)",
                (c.name, c.description, c.valid_months),
            ).lastrowid
            conn.executemany(
                "INSERT INTO role_certifications (role_id, certification_id) VALUES (?, ?)",
                [(role_ids[role], cert_id) for role in c.roles],
            )
        conn.executemany(
            "UPDATE level_labels SET label = ? WHERE scale = ? AND level = ?",
            [(text, scale, level) for scale, texts in catalogue.labels.items() for level, text in texts.items()],
        )


# ---------- demo data (invented people only) ----------

DEMO_ROUNDS = [("2026-Q1", "2026-03-16"), ("2026-Q2", "2026-06-15"), ("2026-Q3", "2026-09-14")]
# name -> role per round (Kim becomes full-stack in Q3)
DEMO_PEOPLE = {
    "Robin (demo)": ["Front-end Developer"] * 3,
    "Sam (demo)": ["Back-end Developer"] * 3,
    "Kim (demo)": ["Front-end Developer", "Front-end Developer", "Full-stack Developer"],
    "Noor (demo)": ["Tech Lead"] * 3,
    "Jo (demo)": ["Front-end Developer"] * 3,
    "Ari (demo)": ["Back-end Developer"] * 3,
}
# name -> team per round (Kim moves from Front end to Back end with the role change; Noor had no team in Q1)
DEMO_TEAMS = {
    "Robin (demo)": ["Front end"] * 3,
    "Sam (demo)": ["Back end"] * 3,
    "Kim (demo)": ["Front end", "Front end", "Back end"],
    "Noor (demo)": [None, "Back end", "Back end"],
    "Jo (demo)": ["Front end"] * 3,
    "Ari (demo)": ["Back end"] * 3,
}
# categories each demo team uses; the first three are shared
_SHARED = ["Version Control & Collaboration", "Testing & Quality", "Ways of Working"]
DEMO_TEAM_CATEGORIES = {
    "Front end": _SHARED + ["Front-end Development", "User Experience"],
    "Back end": _SHARED + ["Back-end Development", "Operations"],
}
DEMO_ADDED_IN_Q2 = "AI coding assistants (Copilot / Claude Code)"  # not rated in Q1, as if added later
DEMO_RETIRED = ("jQuery", "2026-07-01")  # rated in Q1-Q2 only
# Fixed story lines on top of the random ratings: (person, skill) -> self-rating per round
DEMO_STORIES = {
    ("Jo (demo)", "Accessibility (WCAG)"): [2, 3, 5],
    ("Robin (demo)", "Unit testing"): [1, 2, 3],
    ("Noor (demo)", "Containers (Docker)"): [3, 3, 5],
    ("Noor (demo)", "Front-end build tools (Vite / bundlers)"): [1, 2, 2],  # nobody at 3+: a team gap
}
# As if changed in Admin: one on-call expert is thought enough, so Noor being the only one is a key-person risk
DEMO_EXPERTS_NEEDED = {"Incident response & on-call": 1}


def _step(level: int, steps: int, scale: tuple[int, ...] = cat.LEVELS) -> int:
    i = scale.index(level) + steps
    return scale[max(0, min(len(scale) - 1, i))]


def load_demo(conn: sqlite3.Connection, seed: int = 42) -> None:
    """Invented people over three rounds in two teams: progress, a role and team change, a skill added and one
    retired, goals and certifications."""
    rng = random.Random(seed)
    with conn:
        skills = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM skills")}
        roles = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM roles")}
        target = {(r["role_id"], r["skill_id"]): r["level"] for r in conn.execute("SELECT * FROM targets")}
        retired_name, retired_on = DEMO_RETIRED
        conn.execute("UPDATE skills SET retired_on = ? WHERE name = ?", (retired_on, retired_name))
        conn.executemany(
            "UPDATE skills SET experts_needed = ? WHERE name = ?",
            [(n, name) for name, n in DEMO_EXPERTS_NEEDED.items()],
        )

        round_ids = [
            conn.execute("INSERT INTO rounds (label, held_on) VALUES (?, ?)", r).lastrowid for r in DEMO_ROUNDS
        ]
        teams = {
            name: conn.execute("INSERT INTO teams (name) VALUES (?)", (name,)).lastrowid
            for name in sorted({t for ts in DEMO_TEAMS.values() for t in ts if t})
        }
        conn.executemany(
            "INSERT INTO team_categories (team_id, category) VALUES (?, ?)",
            [(teams[t], c) for t, cats in DEMO_TEAM_CATEGORIES.items() for c in cats],
        )
        for person, person_roles in DEMO_PEOPLE.items():
            person_id = conn.execute("INSERT INTO people (name) VALUES (?)", (person,)).lastrowid
            # starting point per skill: around the first role's target, usually a little below it
            first_role = roles[person_roles[0]]
            level = {
                s: _step(target[(first_role, sid)], rng.choice([-2, -1, -1, 0, 0, 1])) for s, sid in skills.items()
            }
            desire = {s: rng.choice(cat.DESIRES) for s in skills}
            for r, (round_id, role, team) in enumerate(zip(round_ids, person_roles, DEMO_TEAMS[person])):
                aid = conn.execute(
                    "INSERT INTO assessments (person_id, round_id, role_id, team_id) VALUES (?, ?, ?, ?)",
                    (person_id, round_id, roles[role], teams.get(team)),
                ).lastrowid
                rows = []
                for s, sid in skills.items():
                    if (s == DEMO_ADDED_IN_Q2 and r == 0) or (s == retired_name and r == 2):
                        continue
                    if r > 0 and rng.random() < 0.2:  # some growth each round
                        level[s] = _step(level[s], 1)
                    if r > 0 and rng.random() < 0.15:  # interests shift a bit
                        desire[s] = _step(desire[s], rng.choice([-1, 1]), cat.DESIRES)
                    if rng.random() < 0.04:  # occasionally not assessed
                        continue
                    story = DEMO_STORIES.get((person, s), [None] * 3)[r]
                    self_ = level[s] if story is None else story
                    agreed = _step(self_, rng.choice([-1, 1])) if rng.random() < 0.08 else None
                    if story is not None:
                        agreed = None  # story lines are the level used, exactly
                    rows.append((aid, sid, self_, agreed, desire[s]))
                conn.executemany(
                    "INSERT INTO ratings (assessment_id, skill_id, self, agreed, desire) VALUES (?, ?, ?, ?, ?)", rows
                )

        people = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM people")}
        q1, q2, q3 = round_ids
        goals = [
            (
                "Jo (demo)",
                "Accessibility (WCAG)",
                "Make the sign-up flow pass a WCAG audit",
                5,
                q1,
                q2,
                "done",
                "Screen-reader testing course; audit the forms with Robin",
            ),
            (
                "Robin (demo)",
                "Unit testing",
                "Cover the shared components with unit tests",
                5,
                q1,
                q3,
                "in_progress",
                "Add tests for the shared components; review tests with Ari",
            ),
            (
                "Noor (demo)",
                "Containers (Docker)",
                "Run every service in containers",
                8,
                q2,
                None,
                "open",
                "Container course; move one service a sprint",
            ),
            ("Sam (demo)", None, "Present the API roadmap at the team meeting", None, q2, q3, "dropped", ""),
        ]
        conn.executemany(
            "INSERT INTO goals (person_id, skill_id, title, target_level, created_round_id, due_round_id, status, actions)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(people[p], skills[s] if s else None, t, lvl, c, d, st, a) for p, s, t, lvl, c, d, st, a in goals],
        )

        # certifications: (name, description, valid months or None, roles that require it)
        certs = [
            (
                "Security awareness training",
                "Yearly internal course",
                12,
                ["Front-end Developer", "Back-end Developer", "Full-stack Developer", "Tech Lead"],
            ),
            ("Cloud practitioner", "Vendor foundation certification", 36, ["Back-end Developer"]),
            ("On-call induction", "Required before joining the on-call rota", None, ["Tech Lead"]),
        ]
        cert_ids = {}
        for name, desc, months, needed_by in certs:
            cert_ids[name] = conn.execute(
                "INSERT INTO certifications (name, description, valid_months) VALUES (?, ?, ?)", (name, desc, months)
            ).lastrowid
            conn.executemany(
                "INSERT INTO role_certifications (role_id, certification_id) VALUES (?, ?)",
                [(roles[r], cert_ids[name]) for r in needed_by],
            )
        # (person, certification, obtained, expires): one renewed, one expired, one never expires; Sam has none
        records = [
            ("Jo (demo)", "Security awareness training", "2025-01-20", "2026-01-20"),
            ("Jo (demo)", "Security awareness training", "2026-01-15", "2027-01-15"),
            ("Robin (demo)", "Security awareness training", "2025-03-10", "2026-03-10"),
            ("Noor (demo)", "Security awareness training", "2025-11-20", "2026-11-20"),
            ("Noor (demo)", "On-call induction", "2025-11-03", None),
            ("Ari (demo)", "Cloud practitioner", "2026-02-02", "2029-02-02"),
        ]
        conn.executemany(
            "INSERT INTO person_certifications (person_id, certification_id, obtained_on, expires_on)"
            " VALUES (?, ?, ?, ?)",
            [(people[p], cert_ids[c], o, e) for p, c, o, e in records],
        )


def create_database(path: Path, demo: bool = False, catalogue: Path | None = None) -> cat.Catalogue:
    """Create a new database at path from a catalogue file. Refuses if the file already exists.

    The catalogue is read and checked before anything is created. Without one, a real database gets the
    data catalogue and the demo the dev-team one; the demo's people are built on that catalogue's skills
    and roles, so demo=True can't take another.
    """
    if path.exists():
        raise FileExistsError(f"{path} already exists; not overwriting. Delete it yourself to start again.")
    if catalogue is None:
        catalogue = cat.DEMO if demo else cat.DEFAULT
    if demo and Path(catalogue).resolve() != cat.DEMO.resolve():
        raise ValueError("the demo database is built on its own catalogue (dev); leave out --catalogue")
    loaded = cat.load(catalogue)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        migrate(conn)
        load_catalogue(conn, loaded)
        if demo:
            load_demo(conn)
    except Exception:
        conn.close()
        path.unlink(missing_ok=True)  # don't leave a half-built database behind
        raise
    conn.close()
    return loaded
