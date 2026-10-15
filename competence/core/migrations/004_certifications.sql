-- Certifications: yes/no with an expiry date ("trained on X, valid until Y"), which the 0-8 scale handles badly.
-- Not tied to rounds: they are current, dated facts. Status (valid, expiring, expired, missing) is worked out
-- from the dates whenever it's shown, never stored.

CREATE TABLE certifications (
    id           INTEGER PRIMARY KEY,
    name         TEXT    NOT NULL UNIQUE,
    description  TEXT    NOT NULL DEFAULT '',
    valid_months INTEGER CHECK (valid_months IS NULL OR valid_months BETWEEN 1 AND 600),  -- NULL = never expires
    retired_on   TEXT    CHECK (retired_on IS NULL OR date(retired_on) IS retired_on)
) STRICT;

-- One row each time someone obtains or renews a certification; the newest (by obtained_on) counts.
-- expires_on starts as obtained_on + valid_months but can be changed (NULL = never expires).
CREATE TABLE person_certifications (
    id               INTEGER PRIMARY KEY,
    person_id        INTEGER NOT NULL REFERENCES people (id),
    certification_id INTEGER NOT NULL REFERENCES certifications (id),
    obtained_on      TEXT    NOT NULL CHECK (date(obtained_on) IS obtained_on),
    expires_on       TEXT    CHECK (expires_on IS NULL OR (date(expires_on) IS expires_on AND expires_on >= obtained_on)),
    notes            TEXT    NOT NULL DEFAULT '',
    UNIQUE (person_id, certification_id, obtained_on)
) STRICT;

-- Which certifications a role requires. A person's role is the one from their newest assessment.
CREATE TABLE role_certifications (
    role_id          INTEGER NOT NULL REFERENCES roles (id),
    certification_id INTEGER NOT NULL REFERENCES certifications (id),
    PRIMARY KEY (role_id, certification_id)
) STRICT;

CREATE INDEX person_certifications_cert ON person_certifications (certification_id);
