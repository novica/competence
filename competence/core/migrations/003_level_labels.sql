-- What each point of the scale means, shown to raters. Editable in Admin; a catalogue file can set its own.
-- Defaults are the texts from the workbook's Guide tab.

CREATE TABLE level_labels (
    scale TEXT    NOT NULL CHECK (scale IN ('level', 'desire')),
    level INTEGER NOT NULL CHECK (level IN (0, 1, 2, 3, 5, 8)),
    label TEXT    NOT NULL CHECK (label != ''),
    PRIMARY KEY (scale, level)
) STRICT;

INSERT INTO level_labels (scale, level, label) VALUES
    ('level', 0, 'None — no knowledge or exposure'),
    ('level', 1, 'Awareness — knows what it is and why it matters'),
    ('level', 2, 'Beginner — can do simple tasks with guidance'),
    ('level', 3, 'Practitioner — works independently on routine tasks'),
    ('level', 5, 'Advanced — handles complex work, helps others'),
    ('level', 8, 'Expert — go-to person, sets standards, teaches'),
    ('desire', 0, 'No interest'),
    ('desire', 1, 'Low — would do it if needed'),
    ('desire', 2, 'Some — open to learning'),
    ('desire', 3, 'Interested — would like to grow here'),
    ('desire', 5, 'Keen — actively wants projects using this'),
    ('desire', 8, 'Passionate — wants to specialise / lead this');
