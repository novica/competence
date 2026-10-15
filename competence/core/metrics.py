"""The method's calculations, as plain functions. One definition of each number for every view.

The per-skill figures, flags and person figures copy the legacy workbook's formulas, with two exceptions.
Desire is 1-3 here: the article's three points (not interested, happy to do it, passionate) moved up by one, so
competency doesn't drop to 0 for a skill nobody wants (the workbook used 0-8, a misreading of the article's table).
And where the workbook averaged levels, desire or competency, this takes the lower median: the scale is ordinal
and unevenly spaced (0, 1, 2, 3, 5, 8), so an average like 4.3 is a level nobody holds; the lower median is
always a point on the scale.
tests/test_metrics_workbook.py checks the rest against values calculated by Excel.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import median_low

TEAM_GAP = "Team gap"
BELOW_EXPERT_TARGET = "Below expert target"
KEY_PERSON_RISK = "Key-person risk"
# In the order skill_flag checks them; the in-app guide shows these texts.
FLAG_RULES = {
    TEAM_GAP: "Nobody is at level 3 or more: nobody can do it independently.",
    BELOW_EXPERT_TARGET: "Fewer people at 5 or more than the skill's experts needed.",
    KEY_PERSON_RISK: "A priority-3 skill with exactly one person at 5 or more: the team depends on them.",
}


def level_used(self_level: int | None, agreed: int | None) -> int | None:
    """Agreed rating if peer review set one, otherwise the self-rating."""
    return agreed if agreed is not None else self_level


def gap(level: int | None, target: int | None) -> int | None:
    """Level minus role target. Negative = below target. None if either is missing."""
    if level is None or target is None:
        return None
    return level - target


def competency(level: int | None, desire: int | None) -> int | None:
    """Level x desire (0-24), only when both are known. No interest (1) leaves the level as it is, passion (3) triples it."""
    if level is None or desire is None:
        return None
    return level * desire


def is_dev_candidate(level: int | None, desire: int | None) -> bool:
    """Passionate about it (desire 3) but isn't there yet (level 2 or below)."""
    return level is not None and desire is not None and desire >= 3 and level <= 2


def median(values: Iterable[int | None]) -> int | None:
    """Lower median of the known values (of 3 and 5: 3), so it stays on the scale. None if there are none."""
    xs = [v for v in values if v is not None]
    return median_low(xs) if xs else None


# ---------- one skill across the team ----------


@dataclass(frozen=True)
class Observation:
    """One person's values for one skill in one round."""

    used: int | None
    desire: int | None
    target: int | None


@dataclass(frozen=True)
class SkillStats:
    assessed: int
    median_level: int | None
    median_desire: int | None
    max_level: int | None
    n3: int  # people at 3+ (can work independently)
    n5: int  # people at 5+ (advanced)
    below: int  # people below their role target
    shortfall: int  # points below target, summed over people
    weighted_shortfall: int  # priority x shortfall
    dev_candidates: int
    median_competency: int | None
    flag: str | None


def skill_flag(assessed: int, n3: int, n5: int, priority: int, experts_needed: int) -> str | None:
    """Checked in this order, as in the workbook. None when nobody is assessed or nothing is wrong."""
    if assessed == 0:
        return None
    if n3 == 0:
        return TEAM_GAP
    if n5 < experts_needed:
        return BELOW_EXPERT_TARGET
    if n5 == 1 and priority == 3:
        return KEY_PERSON_RISK
    return None


def skill_stats(obs: Sequence[Observation], priority: int, experts_needed: int) -> SkillStats:
    levels = [o.used for o in obs if o.used is not None]
    gaps = [g for o in obs if (g := gap(o.used, o.target)) is not None]
    shortfall = -sum(g for g in gaps if g < 0)
    n3 = sum(lv >= 3 for lv in levels)
    n5 = sum(lv >= 5 for lv in levels)
    return SkillStats(
        assessed=len(levels),
        median_level=median(levels),
        median_desire=median(o.desire for o in obs),
        max_level=max(levels) if levels else None,
        n3=n3,
        n5=n5,
        below=sum(g < 0 for g in gaps),
        shortfall=shortfall,
        weighted_shortfall=priority * shortfall,
        dev_candidates=sum(is_dev_candidate(o.used, o.desire) for o in obs),
        median_competency=median(competency(o.used, o.desire) for o in obs),
        flag=skill_flag(len(levels), n3, n5, priority, experts_needed),
    )


@dataclass(frozen=True)
class CategoryStats:
    category: str
    skills: int
    median_level: int | None  # median of the skills' medians
    median_desire: int | None
    weighted_shortfall: int
    flagged: int


def category_stats(items: Sequence[tuple[str, SkillStats]]) -> list[CategoryStats]:
    """Roll per-skill stats up by category, in the order categories first appear."""
    order: dict[str, list[SkillStats]] = {}
    for category, st in items:
        order.setdefault(category, []).append(st)
    return [
        CategoryStats(
            c,
            len(sts),
            median(s.median_level for s in sts),
            median(s.median_desire for s in sts),
            sum(s.weighted_shortfall for s in sts),
            sum(s.flag is not None for s in sts),
        )
        for c, sts in order.items()
    ]


@dataclass(frozen=True)
class Headline:
    people_assessed: int
    pct_at_target: float | None  # share of rated (person, skill) pairs with a target that are at or above it
    weighted_shortfall: int
    team_gaps: int
    key_person_risks: int
    below_expert_target: int
    median_competency: int | None  # over all rated (person, skill) pairs

    @property
    def weighted_shortfall_per_person(self) -> float | None:
        """The total grows with team size; per person assessed, teams and rounds can be compared."""
        return self.weighted_shortfall / self.people_assessed if self.people_assessed else None

    def delta(self, before: "Headline | None", field: str) -> int | None:
        """The change between the whole numbers shown, so 49.6% -> 50.4% is 50 -> 50, no change."""
        if before is None:
            return None
        now, then = getattr(self, field), getattr(before, field)
        return None if now is None or then is None else round(now) - round(then)


def headline(obs_by_person: dict[int, Sequence[Observation]], stats: Sequence[SkillStats]) -> Headline:
    all_obs = [o for obs in obs_by_person.values() for o in obs]
    gaps = [g for o in all_obs if (g := gap(o.used, o.target)) is not None]
    return Headline(
        people_assessed=sum(any(o.used is not None for o in obs) for obs in obs_by_person.values()),
        pct_at_target=100 * sum(g >= 0 for g in gaps) / len(gaps) if gaps else None,
        weighted_shortfall=sum(s.weighted_shortfall for s in stats),
        team_gaps=sum(s.flag == TEAM_GAP for s in stats),
        key_person_risks=sum(s.flag == KEY_PERSON_RISK for s in stats),
        below_expert_target=sum(s.flag == BELOW_EXPERT_TARGET for s in stats),
        median_competency=median(competency(o.used, o.desire) for o in all_obs),
    )


# ---------- one person across skills ----------
# These take rating rows (core.models.RatingRow or anything with the same attributes):
# priority, target, used, desire, gap, prev_used, prev_desire, compared.


def priority_gap(row) -> int | None:
    """Priority x points below role target (0 if at or above target)."""
    return None if row.gap is None else row.priority * max(0, -row.gap)


@dataclass(frozen=True)
class PersonSummary:
    assessed: int
    median_level: int | None
    below: int
    shortfall: int
    weighted_shortfall: int
    median_competency: int | None
    dev_candidates: int


def person_summary(rows: Sequence) -> PersonSummary:
    gaps = [r.gap for r in rows if r.gap is not None]
    return PersonSummary(
        assessed=sum(r.used is not None for r in rows),
        median_level=median(r.used for r in rows),
        below=sum(g < 0 for g in gaps),
        shortfall=-sum(g for g in gaps if g < 0),
        weighted_shortfall=sum(pg for r in rows if (pg := priority_gap(r))),
        median_competency=median(competency(r.used, r.desire) for r in rows),
        dev_candidates=sum(is_dev_candidate(r.used, r.desire) for r in rows),
    )


def top_gaps(rows: Sequence, n: int = 10) -> list:
    """Largest priority gaps first; ties keep catalogue order. Only rows actually below target."""
    ranked = [(i, r) for i, r in enumerate(rows) if (priority_gap(r) or 0) > 0]
    ranked.sort(key=lambda ir: (-priority_gap(ir[1]), ir[0]))
    return [r for _, r in ranked[:n]]


def top_interests(rows: Sequence, n: int = 10) -> list:
    """Desire 2 or 3, highest desire first, then lowest current level (blank counts as 0); ties keep catalogue order."""
    ranked = [(i, r) for i, r in enumerate(rows) if r.desire is not None and r.desire >= 2]
    ranked.sort(key=lambda ir: (-ir[1].desire, ir[1].used if ir[1].used is not None else 0, ir[0]))
    return [r for _, r in ranked[:n]]


def changes_since_last_round(rows: Sequence) -> list:
    """Rows whose level used or desire differ from last round (only when there is a last round)."""
    return [r for r in rows if r.compared and (r.used != r.prev_used or r.desire != r.prev_desire)]
