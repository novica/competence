"""Metrics cases the one-person workbook example can't reach: several people, every flag branch, headline."""

from dataclasses import dataclass

import pytest

from competence.core import metrics
from competence.core.metrics import Observation as O


def test_small_helpers():
    assert metrics.level_used(3, None) == 3 and metrics.level_used(3, 5) == 5 and metrics.level_used(None, None) is None
    assert metrics.gap(3, 5) == -2 and metrics.gap(None, 5) is None and metrics.gap(3, None) is None
    assert metrics.competency(3, 2) == 6 and metrics.competency(None, 2) is None
    assert metrics.competency(8, 1) == 8 and metrics.competency(8, 3) == 24 and metrics.competency(0, 3) == 0  # 0-24
    # desire 3 (passionate) and level 2 or below
    assert metrics.is_dev_candidate(2, 3) and metrics.is_dev_candidate(0, 3)
    assert not any(metrics.is_dev_candidate(level, desire) for level, desire in ((3, 3), (2, 2), (None, 3)))
    assert metrics.median([1, None, 3, 5]) == 3 and metrics.median([3, 5]) == 3 and metrics.median([None]) is None


def test_skill_stats_several_people():
    obs = [O(5, 2, 3), O(2, 3, 5), O(None, 3, 3), O(8, None, 5), O(0, 2, None)]
    s = metrics.skill_stats(obs, priority=2, experts_needed=2)
    assert s.assessed == 4  # the blank level doesn't count
    assert s.median_level == 2  # 0, 2, 5, 8: the lower middle one
    assert s.median_desire == 2  # 2, 2, 3, 3
    assert s.max_level == 8 and s.n3 == 2 and s.n5 == 2
    assert s.below == 1 and s.shortfall == 3 and s.weighted_shortfall == 6  # only 2 vs 5 is below
    assert s.dev_candidates == 1  # level 2, desire 3
    assert s.median_competency == 6  # 0, 6, 10
    assert s.flag is None


@pytest.mark.parametrize(
    "assessed, n3, n5, priority, experts, flag",
    [
        (0, 0, 0, 3, 2, None),
        (2, 0, 0, 3, 0, metrics.TEAM_GAP),
        (2, 2, 1, 3, 2, metrics.BELOW_EXPERT_TARGET),  # checked before key-person risk
        (2, 2, 1, 3, 1, metrics.KEY_PERSON_RISK),
        (2, 2, 1, 2, 1, None),  # one expert is only a risk for priority 3
        (2, 2, 2, 3, 2, None),
        (2, 1, 0, 1, 0, None),
    ],
)
def test_flag_order(assessed, n3, n5, priority, experts, flag):
    assert metrics.skill_flag(assessed, n3, n5, priority, experts) == flag


def test_headline():
    by_person = {1: [O(5, 1, 3), O(2, 2, 5)], 2: [O(3, None, 3)], 3: [O(None, 2, 3)], 4: []}
    stats = [
        metrics.skill_stats([O(5, 1, 3), O(3, None, 3)], 3, 1),
        metrics.skill_stats([O(2, 2, 5)], 2, 1),
        metrics.skill_stats([O(None, 2, 3)], 1, 0),
    ]
    h = metrics.headline(by_person, stats)
    assert h.people_assessed == 2  # person 3 has only a desire, person 4 nothing
    assert h.pct_at_target == pytest.approx(100 * 2 / 3)
    assert h.weighted_shortfall == 6 and h.weighted_shortfall_per_person == 3
    assert metrics.headline({}, []).weighted_shortfall_per_person is None
    assert (h.team_gaps, h.below_expert_target, h.key_person_risks) == (1, 0, 1)
    assert h.median_competency == 4  # 4 and 5: the lower one
    earlier = metrics.headline({1: [O(3, 1, 3)]}, [metrics.skill_stats([O(3, 1, 3)], 3, 1)])
    assert h.delta(earlier, "people_assessed") == 1
    assert h.delta(None, "people_assessed") is None
    shown = metrics.Headline(6, 50.4, 0, 0, 0, 0, None)
    assert shown.delta(metrics.Headline(6, 49.6, 0, 0, 0, 0, None), "pct_at_target") == 0  # 50 -> 50 as shown


def test_category_medians():
    """A category's median is the lower median of its skills' medians; unrated skills don't count."""

    def skill(level, desire):
        return metrics.skill_stats([O(level, desire, None)], 2, 1)

    items = [("A", skill(2, 1)), ("A", skill(5, 3)), ("A", skill(None, None)), ("A", skill(8, 2)), ("B", skill(3, 2))]
    a, b = metrics.category_stats(items)
    assert (a.category, a.skills, a.median_level, a.median_desire) == ("A", 4, 5, 2)  # 2, 5, 8 and 1, 2, 3
    assert (b.category, b.median_level, b.median_desire) == ("B", 3, 2)


def test_person_summary_medians():
    rows = [R("a", 1, 5, 2, 3), R("b", 1, 5, 8, 1), R("c", 1, 3, 5, 2), R("d", 1, 3, None, 2), R("e", 1, 3, 3, None)]
    s = metrics.person_summary(rows)
    assert s.assessed == 4 and s.median_level == 3  # 2, 3, 5, 8: the lower middle one
    assert s.median_competency == 8  # 6, 8, 10 (no competency without both)


@dataclass(frozen=True)
class R:
    name: str
    priority: int
    target: int | None
    used: int | None
    desire: int | None
    compared: bool = True
    prev_used: int | None = None
    prev_desire: int | None = None

    @property
    def gap(self):
        return metrics.gap(self.used, self.target)


def test_top_lists_order_and_ties():
    rows = [
        R("a", 1, 5, 2, 3),
        R("b", 3, 5, 3, 3),
        R("c", 2, 5, 2, 2),
        R("d", 3, 3, 5, 2),
        R("e", 2, 5, 2, None),
        R("f", 1, 5, 5, 1),
    ]
    # priority gaps: a 3, b 6, c 6, d 0, e 6 -> b, c, e (catalogue order on ties), then a
    assert [r.name for r in metrics.top_gaps(rows)] == ["b", "c", "e", "a"]
    assert [r.name for r in metrics.top_gaps(rows, n=2)] == ["b", "c"]
    # desire 2 or 3: a 3 (level 2), b 3 (level 3), c 2 (level 2), d 2 (level 5); f (desire 1) and e (blank) left out
    assert [r.name for r in metrics.top_interests(rows)] == ["a", "b", "c", "d"]


def test_changes_since_last_round():
    rows = [
        R("same", 1, 3, 3, 1, prev_used=3, prev_desire=1),
        R("up", 1, 3, 5, 1, prev_used=3, prev_desire=1),
        R("desire", 1, 3, 3, 2, prev_used=3, prev_desire=1),
        R("new", 1, 3, 2, None),
        R("first", 1, 3, 2, 2, compared=False),
    ]
    assert [r.name for r in metrics.changes_since_last_round(rows)] == ["up", "desire", "new"]
