-- Desire on the article's scale: 0 = does not interest me, 1 = happy to perform this work, 2 = passionate to continue
-- learning. Skill level stays 0, 1, 2, 3, 5, 8. Earlier desire values (0-8) are mapped 0 -> 0, 1-3 -> 1, 5-8 -> 2.
-- SQLite can't change a CHECK, so ratings is rebuilt; the migration runner checks every reference before committing.

CREATE TABLE ratings_new (
    assessment_id INTEGER NOT NULL REFERENCES assessments (id),
    skill_id      INTEGER NOT NULL REFERENCES skills (id),
    self          INTEGER CHECK (self   IS NULL OR self   IN (0, 1, 2, 3, 5, 8)),
    agreed        INTEGER CHECK (agreed IS NULL OR agreed IN (0, 1, 2, 3, 5, 8)),
    desire        INTEGER CHECK (desire IS NULL OR desire IN (0, 1, 2)),
    PRIMARY KEY (assessment_id, skill_id)
) STRICT;

INSERT INTO ratings_new (assessment_id, skill_id, self, agreed, desire)
SELECT assessment_id, skill_id, self, agreed,
       CASE WHEN desire IS NULL THEN NULL WHEN desire = 0 THEN 0 WHEN desire <= 3 THEN 1 ELSE 2 END
FROM ratings;

DROP TABLE ratings;
ALTER TABLE ratings_new RENAME TO ratings;
CREATE INDEX ratings_skill ON ratings (skill_id);

DELETE FROM level_labels WHERE scale = 'desire' AND level NOT IN (0, 1, 2);
UPDATE level_labels SET label = CASE level
    WHEN 0 THEN 'Does not interest me'
    WHEN 1 THEN 'Happy to perform this work'
    WHEN 2 THEN 'Passionate to continue learning' END
WHERE scale = 'desire';
