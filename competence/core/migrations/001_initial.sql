-- Initial schema. Ratings are stored one row per (assessment, skill), linked to permanent ids,
-- so renaming or retiring skills and roles never breaks history.
-- Levels use the Fibonacci scale 0,1,2,3,5,8; NULL means "not assessed".

CREATE TABLE skills (
    id             INTEGER PRIMARY KEY,
    category       TEXT    NOT NULL,
    name           TEXT    NOT NULL UNIQUE,
    description    TEXT    NOT NULL DEFAULT '',
    ecosystem      TEXT    NOT NULL CHECK (ecosystem IN ('R', 'Python', 'General')),
    priority       INTEGER NOT NULL CHECK (priority BETWEEN 1 AND 3),
    experts_needed INTEGER NOT NULL CHECK (experts_needed >= 0),
    sort_order     INTEGER NOT NULL,
    retired_on     TEXT    CHECK (retired_on IS NULL OR date(retired_on) IS retired_on)
) STRICT;

CREATE TABLE roles (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL,
    retired_on TEXT    CHECK (retired_on IS NULL OR date(retired_on) IS retired_on)
) STRICT;

-- A missing row means "no target" (shown blank), which is different from a target of 0.
CREATE TABLE targets (
    role_id  INTEGER NOT NULL REFERENCES roles (id),
    skill_id INTEGER NOT NULL REFERENCES skills (id),
    level    INTEGER NOT NULL CHECK (level IN (0, 1, 2, 3, 5, 8)),
    PRIMARY KEY (role_id, skill_id)
) STRICT;

CREATE TABLE people (
    id     INTEGER PRIMARY KEY,
    name   TEXT    NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
) STRICT;

CREATE TABLE rounds (
    id      INTEGER PRIMARY KEY,
    label   TEXT    NOT NULL UNIQUE,
    held_on TEXT    CHECK (held_on IS NULL OR date(held_on) IS held_on),
    notes   TEXT    NOT NULL DEFAULT ''
) STRICT;

-- The person's role at that round, so a later role change doesn't rewrite the past.
CREATE TABLE assessments (
    id        INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people (id),
    round_id  INTEGER NOT NULL REFERENCES rounds (id),
    role_id   INTEGER NOT NULL REFERENCES roles (id),
    notes     TEXT    NOT NULL DEFAULT '',
    UNIQUE (person_id, round_id)
) STRICT;

-- Level used = COALESCE(agreed, self).
CREATE TABLE ratings (
    assessment_id INTEGER NOT NULL REFERENCES assessments (id),
    skill_id      INTEGER NOT NULL REFERENCES skills (id),
    self          INTEGER CHECK (self   IS NULL OR self   IN (0, 1, 2, 3, 5, 8)),
    agreed        INTEGER CHECK (agreed IS NULL OR agreed IN (0, 1, 2, 3, 5, 8)),
    desire        INTEGER CHECK (desire IS NULL OR desire IN (0, 1, 2, 3, 5, 8)),
    PRIMARY KEY (assessment_id, skill_id)
) STRICT;

-- Progress is worked out from ratings (latest level used vs target_level), never stored.
CREATE TABLE goals (
    id               INTEGER PRIMARY KEY,
    person_id        INTEGER NOT NULL REFERENCES people (id),
    skill_id         INTEGER REFERENCES skills (id),          -- NULL = general goal
    title            TEXT    NOT NULL,
    target_level     INTEGER CHECK (target_level IS NULL OR target_level IN (0, 1, 2, 3, 5, 8)),
    created_round_id INTEGER NOT NULL REFERENCES rounds (id),
    due_round_id     INTEGER REFERENCES rounds (id),          -- NULL = no deadline
    status           TEXT    NOT NULL DEFAULT 'open'
                     CHECK (status IN ('open', 'in_progress', 'done', 'dropped')),
    actions          TEXT    NOT NULL DEFAULT '',
    notes            TEXT    NOT NULL DEFAULT ''
) STRICT;

CREATE INDEX ratings_skill ON ratings (skill_id);
CREATE INDEX assessments_round ON assessments (round_id);
CREATE INDEX goals_person ON goals (person_id);
