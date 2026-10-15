-- Which skill categories each team uses. A team with no rows uses every category, so a single team and data from
-- before this step are unchanged. Categories are free text on skills; core.admin keeps these names in step when the
-- last skill of a category moves to another one (which is how a category gets renamed).

CREATE TABLE team_categories (
    team_id  INTEGER NOT NULL REFERENCES teams (id),
    category TEXT    NOT NULL,
    PRIMARY KEY (team_id, category)
) STRICT;
