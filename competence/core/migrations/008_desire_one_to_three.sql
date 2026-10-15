-- Desire moves from the article's 0, 1, 2 to 1, 2, 3 (not interested, happy to do it, passionate), so that
-- competency (level x desire) no longer drops to 0 for skills someone has no interest in. Every stored desire goes up
-- by one, and the desire texts move with their points, so edited texts are kept.
-- SQLite can't change a CHECK, so ratings is rebuilt; the migration runner checks every reference before committing.

CREATE TABLE ratings_new (
    assessment_id INTEGER NOT NULL REFERENCES assessments (id),
    skill_id      INTEGER NOT NULL REFERENCES skills (id),
    self          INTEGER CHECK (self   IS NULL OR self   IN (0, 1, 2, 3, 5, 8)),
    agreed        INTEGER CHECK (agreed IS NULL OR agreed IN (0, 1, 2, 3, 5, 8)),
    desire        INTEGER CHECK (desire IS NULL OR desire IN (1, 2, 3)),
    PRIMARY KEY (assessment_id, skill_id)
) STRICT;

INSERT INTO ratings_new (assessment_id, skill_id, self, agreed, desire)
SELECT assessment_id, skill_id, self, agreed, desire + 1 FROM ratings;

DROP TABLE ratings;
ALTER TABLE ratings_new RENAME TO ratings;
CREATE INDEX ratings_skill ON ratings (skill_id);

-- Moved through a helper table: shifting in place would collide with the next point's row.
CREATE TABLE desire_labels_old AS SELECT level, label FROM level_labels WHERE scale = 'desire';
DELETE FROM level_labels WHERE scale = 'desire';
INSERT INTO level_labels (scale, level, label) SELECT 'desire', level + 1, label FROM desire_labels_old;
DROP TABLE desire_labels_old;
