"""Form input models. HTML forms send strings; these turn them into checked values."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints


def _blank_to_none(v):
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return None
    return int(v) if isinstance(v, str) else v


# Skill level on the Fibonacci scale, desire 1-3; a blank form field means "not assessed"
Level = Annotated[Literal[0, 1, 2, 3, 5, 8] | None, BeforeValidator(_blank_to_none)]
Desire = Annotated[Literal[1, 2, 3] | None, BeforeValidator(_blank_to_none)]
OptionalDate = Annotated[date | None, BeforeValidator(lambda v: None if v in ("", None) else v)]


class _Form(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RatingForm(_Form):
    self_level: Level = None
    agreed: Level = None
    desire: Desire = None


class RoundForm(_Form):
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    held_on: OptionalDate = None


class PersonForm(_Form):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


OptionalId = Annotated[Annotated[int, Field(ge=1)] | None, BeforeValidator(_blank_to_none)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class StartForm(_Form):
    role_id: Annotated[int, Field(ge=1)]
    team_id: OptionalId = None  # blank = no team


class TeamChangeForm(_Form):
    team_id: OptionalId = None  # blank = no team


Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Status = Literal["open", "in_progress", "done", "dropped"]


class GoalCreateForm(_Form):
    title: Title
    skill_id: OptionalId = None
    target_level: Level = None
    due_round_id: OptionalId = None
    actions: Text = ""
    notes: Text = ""


class GoalEditForm(_Form):
    title: Title
    target_level: Level = None
    due_round_id: OptionalId = None
    status: Status
    actions: Text = ""
    notes: Text = ""
    round_id: Annotated[int, Field(ge=1)]  # the round the facilitator came from, to go back to


class GoalStatusForm(_Form):
    status: Status
    round_id: Annotated[int, Field(ge=1)]


def _to_int(v):
    return int(v) if isinstance(v, str) and v.strip() != "" else v


Priority = Annotated[Literal[1, 2, 3], BeforeValidator(_to_int)]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Back = Annotated[str, StringConstraints(max_length=300)]


class SkillEditForm(_Form):
    category: Name
    name: Name
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""
    tag: Tag = ""
    priority: Priority
    experts_needed: Annotated[int, Field(ge=0, le=20)]


class SkillCreateForm(SkillEditForm):
    copy_ratings_from: OptionalId = None
    target_role: list[Annotated[int, Field(ge=1)]] = []  # parallel lists: one entry per role
    target_level: list[Level] = []


class RoleCreateForm(_Form):
    name: Name
    copy_targets_from: OptionalId = None


class NameForm(_Form):
    name: Name
    back: Back = ""


class CategoryRenameForm(_Form):
    old: Name
    new: Name


class BackForm(_Form):
    back: Back = ""


class MoveForm(_Form):
    direction: Literal["up", "down"]
    back: Back = ""


class ActiveForm(_Form):
    active: bool
    back: Back = ""


class TargetForm(_Form):
    level: Level = None
    back: Back = ""


Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class LevelLabelsForm(_Form):
    """One text per point of the scale, for skill level and for desire."""

    level_0: Label
    level_1: Label
    level_2: Label
    level_3: Label
    level_5: Label
    level_8: Label
    desire_1: Label
    desire_2: Label
    desire_3: Label

    def labels(self) -> dict[str, dict[int, str]]:
        out: dict[str, dict[int, str]] = {}
        for key, text in self.model_dump().items():
            scale, level = key.split("_")
            out.setdefault(scale, {})[int(level)] = text
        return out


class CategoryTargetForm(_Form):
    category: Name
    tag: Tag = ""
    level: Level = None
    back: Back = ""


Months = Annotated[Annotated[int, Field(ge=1, le=600)] | None, BeforeValidator(_blank_to_none)]


class CertificationForm(_Form):
    name: Name
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""
    valid_months: Months = None  # blank = never expires
    role_id: list[Annotated[int, Field(ge=1)]] = []  # roles that require it (checkboxes)


class RecordForm(_Form):
    certification_id: Annotated[int, Field(ge=1)]
    obtained_on: date
    expires_on: OptionalDate = None  # blank = worked out from how long it lasts
    notes: Text = ""


class TeamCategoriesForm(_Form):
    category: list[Name] = []  # the ticked ones; none = all categories


class RoundEditForm(_Form):
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    held_on: OptionalDate = None
    notes: Text = ""
