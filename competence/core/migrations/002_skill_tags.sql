-- Free-text tags instead of the fixed R / Python / General ecosystems, so other teams can use their own
-- (or none: blank means untagged). SQLite can't drop a CHECK, so the table is rebuilt with the same ids;
-- the migration runner turns foreign keys off meanwhile and checks every reference before committing.

CREATE TABLE skills_new (
    id             INTEGER PRIMARY KEY,
    category       TEXT    NOT NULL,
    name           TEXT    NOT NULL UNIQUE,
    description    TEXT    NOT NULL DEFAULT '',
    tag            TEXT    NOT NULL DEFAULT '',
    priority       INTEGER NOT NULL CHECK (priority BETWEEN 1 AND 3),
    experts_needed INTEGER NOT NULL CHECK (experts_needed >= 0),
    sort_order     INTEGER NOT NULL,
    retired_on     TEXT    CHECK (retired_on IS NULL OR date(retired_on) IS retired_on)
) STRICT;

INSERT INTO skills_new (id, category, name, description, tag, priority, experts_needed, sort_order, retired_on)
SELECT id, category, name, description, ecosystem, priority, experts_needed, sort_order, retired_on FROM skills;

DROP TABLE skills;
ALTER TABLE skills_new RENAME TO skills;
