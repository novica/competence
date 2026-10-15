"""Plain data passed in and out of the core. Nothing here knows about HTTP or HTML."""

from dataclasses import dataclass

from competence.core import metrics


@dataclass(frozen=True)
class Round:
    id: int
    label: str
    held_on: str | None


@dataclass(frozen=True)
class Person:
    id: int
    name: str


@dataclass(frozen=True)
class Role:
    id: int
    name: str


@dataclass(frozen=True)
class Team:
    id: int
    name: str
    retired_on: str | None = None


@dataclass(frozen=True)
class Assessment:
    id: int
    person: Person
    round: Round
    role: Role
    team: Team | None = None  # the person's team at that round; None = no team


@dataclass(frozen=True)
class PersonStatus:
    """One person's state in a round, for the round page."""

    person: Person
    assessment_id: int | None
    role_name: str | None
    rated: int  # skills with at least one value
    last_role_id: int | None  # role in their latest earlier assessment, to pre-select
    last_round_label: str | None  # round a new assessment would pre-fill from
    team: Team | None = None  # team this round
    last_team_id: int | None = None  # team in their latest earlier assessment, to pre-select


@dataclass(frozen=True)
class RatingRow:
    """One skill on the rating form, for one assessment."""

    skill_id: int
    category: str
    name: str
    description: str
    tag: str
    priority: int
    target: int | None
    self_level: int | None
    agreed: int | None
    desire: int | None
    compared: bool  # True if the person has an earlier assessment to compare with
    prev_used: int | None  # last round's level used
    prev_desire: int | None  # last round's desire

    @property
    def used(self) -> int | None:
        return metrics.level_used(self.self_level, self.agreed)

    # Changed-since-last-round flags, one per form field. Self was pre-filled from last round's level
    # used, so that's what it is compared with. Agreed starts blank, so it changed once it says
    # something other than last round's level. Always False when there is no earlier assessment.
    @property
    def self_level_changed(self) -> bool:
        return self.compared and self.self_level != self.prev_used

    @property
    def agreed_changed(self) -> bool:
        return self.compared and self.agreed is not None and self.agreed != self.prev_used

    @property
    def desire_changed(self) -> bool:
        return self.compared and self.desire != self.prev_desire

    @property
    def gap(self) -> int | None:
        return metrics.gap(self.used, self.target)


@dataclass(frozen=True)
class SkillOverview:
    """One row of the team overview."""

    skill_id: int
    category: str
    name: str
    tag: str
    priority: int
    experts_needed: int
    stats: metrics.SkillStats


@dataclass(frozen=True)
class TeamOverview:
    round: Round
    previous_round: Round | None
    skills: list[SkillOverview]
    categories: list[metrics.CategoryStats]
    headline: metrics.Headline
    previous_headline: metrics.Headline | None  # same skills and filter, previous round
    open_goals: int  # current count; goals have no history


@dataclass(frozen=True)
class PersonProfile:
    assessment: Assessment
    previous: Assessment | None
    rows: list[RatingRow]
    summary: metrics.PersonSummary
    top_gaps: list[RatingRow]
    top_interests: list[RatingRow]
    changes: list[RatingRow]


@dataclass(frozen=True)
class HeatCell:
    used: int | None
    desire: int | None
    target: int | None

    @property
    def gap(self) -> int | None:
        return metrics.gap(self.used, self.target)

    @property
    def competency(self) -> int | None:
        return metrics.competency(self.used, self.desire)


@dataclass(frozen=True)
class HeatRow:
    skill_id: int
    category: str
    name: str
    tag: str
    cells: list[HeatCell]  # one per Heatmap.columns entry, same order


@dataclass(frozen=True)
class Heatmap:
    round: Round
    columns: list[Assessment]  # people assessed in this round, by name
    rows: list[HeatRow]  # active skills in catalogue order


GOAL_STATUSES = ("open", "in_progress", "done", "dropped")


@dataclass(frozen=True)
class Goal:
    id: int
    person_id: int
    skill_id: int | None  # None = general goal
    skill_name: str | None
    title: str
    target_level: int | None
    created_round: Round
    due_round: Round | None
    status: str
    actions: str
    notes: str


@dataclass(frozen=True)
class GoalProgress:
    """A goal seen from one round: the person's level used then, and in their previous assessment."""

    goal: Goal
    current: int | None
    previous: int | None
    previous_round: Round | None

    @property
    def delta(self) -> int | None:
        return None if self.current is None or self.previous is None else self.current - self.previous

    @property
    def reached(self) -> bool:
        return (
            self.goal.target_level is not None and self.current is not None and self.current >= self.goal.target_level
        )

    @property
    def is_open(self) -> bool:
        return self.goal.status in ("open", "in_progress")


def _diff(after, before):
    return None if after is None or before is None else after - before


@dataclass(frozen=True)
class SkillComparison:
    """One skill's team figures in two rounds, plus its median level in every round."""

    skill_id: int
    category: str
    name: str
    tag: str
    before: metrics.SkillStats
    after: metrics.SkillStats
    trend: list[int | None]  # median level per round, oldest first (TeamComparison.rounds)
    changed: bool = False  # someone assessed in both rounds has a different level used or desire

    @property
    def level_delta(self) -> int | None:
        return _diff(self.after.median_level, self.before.median_level)

    @property
    def desire_delta(self) -> int | None:
        return _diff(self.after.median_desire, self.before.median_desire)

    @property
    def rated_in_either(self) -> bool:
        return (
            self.before.assessed > 0
            or self.after.assessed > 0
            or self.before.median_desire is not None
            or self.after.median_desire is not None
        )


@dataclass(frozen=True)
class TeamComparison:
    before_round: Round
    after_round: Round
    rounds: list[Round]  # every round, oldest first, for the trend columns
    rows: list[SkillComparison]
    before: metrics.Headline
    after: metrics.Headline
    improvements: list[SkillComparison]
    declines: list[SkillComparison]
    first_rated: list[SkillComparison]  # nobody rated it in the earlier round, someone did in the later one
    retired_between: list[tuple[str, str]]  # (skill, retired_on) retired after the earlier round, up to the later


@dataclass(frozen=True)
class PersonSkillComparison:
    skill_id: int
    category: str
    name: str
    tag: str
    target: int | None  # for the person's role in the later round
    before_used: int | None
    after_used: int | None
    before_desire: int | None
    after_desire: int | None
    trend: list[int | None]  # level used per round, oldest first

    @property
    def level_delta(self) -> int | None:
        return _diff(self.after_used, self.before_used)

    @property
    def changed(self) -> bool:
        return self.before_used != self.after_used or self.before_desire != self.after_desire


@dataclass(frozen=True)
class PersonComparison:
    person: Person
    before_round: Round
    after_round: Round
    before: Assessment | None
    after: Assessment | None
    rounds: list[Round]
    rows: list[PersonSkillComparison]
    improvements: list[PersonSkillComparison]
    declines: list[PersonSkillComparison]
