"""metrics.py must give the same numbers as the legacy workbook's Excel formulas.

tests/fixtures/workbook_example.json holds the workbook's example person (Ada, Data Scientist, 117 skills)
and the Team Overview / Person Profile values Excel calculated from them. Regenerate it only if the
workbook formulas themselves change.

The workbook rates desire 0-8; the app uses 1-3 (the article's three points, moved up by one), so the rules with
a desire threshold (development candidates, top interests) no longer match it and are tested in test_metrics.py.
Formulas that only multiply or average desire still match, given the workbook's own values.

The app uses medians where the workbook averages. Per skill that changes nothing here (the example is one person,
and the median of one value is that value), so those are still checked, against the workbook's averages. Medians
across skills (the category roll-up, the person's level and competency) differ and are tested in test_metrics.py
(test_category_medians, test_person_summary_medians).
"""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from competence.core import metrics

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "workbook_example.json").read_text(encoding="utf-8"))
SKILLS = FIXTURE["skills"]


@dataclass(frozen=True)
class Row:
    """The attributes metrics' person functions read, as RatingRow has them."""

    name: str
    priority: int
    target: int | None
    used: int | None
    desire: int | None

    @property
    def gap(self):
        return metrics.gap(self.used, self.target)


def rows():
    return [
        Row(s["name"], s["priority"], s["target"], metrics.level_used(s["self"], s["agreed"]), s["desire"])
        for s in SKILLS
    ]


def stats():
    return [
        metrics.skill_stats([metrics.Observation(r.used, r.desire, r.target)], s["priority"], s["experts_needed"])
        for r, s in zip(rows(), SKILLS)
    ]


# The app's name for a figure -> the workbook's (which averaged where the app takes the median)
WORKBOOK_NAME = {"median_level": "avg_level", "median_desire": "avg_desire", "median_competency": "avg_competency"}


def close(a, b):
    return (a is None and b is None) or (a is not None and b is not None and a == pytest.approx(b))


def test_fixture_is_the_full_example():
    assert len(SKILLS) == 117 and len(FIXTURE["team_overview"]) == 117
    assert sum(s["agreed"] is not None for s in SKILLS) > 0  # exercises agreed overriding self


@pytest.mark.parametrize(
    "field",
    [
        "assessed",
        "median_level",
        "median_desire",
        "max_level",
        "n3",
        "n5",
        "below",
        "shortfall",
        "weighted_shortfall",
        "median_competency",
        "flag",
    ],
)
def test_team_overview_matches_excel(field):
    for got, want in zip(stats(), FIXTURE["team_overview"]):
        g, w = getattr(got, field), want[WORKBOOK_NAME.get(field, field)]
        assert close(g, w) if field in WORKBOOK_NAME else g == w, (want["skill"], field, g, w)


def test_flags_cover_all_workbook_outcomes():
    assert {s.flag for s in stats()} == {metrics.TEAM_GAP, metrics.BELOW_EXPERT_TARGET, None}


def test_category_rollup_matches_excel():
    got = metrics.category_stats([(s["category"], st) for s, st in zip(SKILLS, stats())])
    want = FIXTURE["categories"]
    assert [c.category for c in got] == [c["category"] for c in want]
    for g, w in zip(got, want):
        assert g.skills == w["skills"] and g.weighted_shortfall == w["weighted_shortfall"] and g.flagged == w["flagged"]


def test_person_summary_matches_excel():
    got, want = metrics.person_summary(rows()), FIXTURE["person_summary"]
    for field, w in want.items():
        if field in ("dev_candidates", "avg_level", "avg_competency"):  # desire threshold, medians (see above)
            continue
        assert close(getattr(got, field), w), (field, getattr(got, field), w)


def test_top_lists_match_excel():
    assert [r.name for r in metrics.top_gaps(rows())] == FIXTURE["top_gaps"]  # top interests: see above
