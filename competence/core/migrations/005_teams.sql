-- Several teams in one database, sharing the catalogue. Like the role, the team is recorded per assessment, so a
-- move doesn't rewrite the past. NULL = no team (everything assessed before teams existed).

CREATE TABLE teams (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL UNIQUE,
    retired_on TEXT    CHECK (retired_on IS NULL OR date(retired_on) IS retired_on)
) STRICT;

ALTER TABLE assessments ADD COLUMN team_id INTEGER REFERENCES teams (id);

CREATE INDEX assessments_team ON assessments (team_id);
