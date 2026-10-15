"""Catalogue files: roles, categories with a default target per role, and skills, read from TOML.

`competence init` loads one into a new database (data.toml unless told otherwise). After seeding,
the database is the source of truth; edit skills, roles and targets in the app, not in the file.

A skill's target for a role is its category's default unless the skill lists its own; a role with
neither has no target (blank), which is not the same as 0. Every file is checked fully before
anything is written, so a typo stops the seed instead of turning up later as a missing target.

`dumps` writes a catalogue back as TOML, so one built in Admin can seed another database.
"""

import json
import re
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

LEVELS = (0, 1, 2, 3, 5, 8)  # skill level (self, agreed, targets, goals): the article's Fibonacci scale
DESIRES = (1, 2, 3)  # desire: not interested / happy to do it / passionate (the article has 0-2)
SCALES = {"level": LEVELS, "desire": DESIRES}  # what level_labels describe, and the points each one has
EXPERTS_FOR_PRIORITY = {3: 2, 2: 1, 1: 0}
MAX_EXPERTS = 20
MAX_TAG = 40
CATALOGUES = Path(__file__).resolve().parent.parent / "catalogues"
DEFAULT = CATALOGUES / "data.toml"
DEMO = CATALOGUES / "dev.toml"  # what the demo database is built on
SHIPPED = ("data", "dev", "empty")


def find(name_or_file: str | Path) -> Path:
    """A shipped catalogue by name (data, dev, empty), otherwise the file itself."""
    return CATALOGUES / f"{name_or_file}.toml" if str(name_or_file) in SHIPPED else Path(name_or_file)


class CatalogueError(ValueError):
    pass


@dataclass(frozen=True)
class Skill:
    category: str
    name: str
    description: str
    tag: str
    targets: dict[str, int]  # role name -> target, in role order; a missing role = no target
    priority: int | None = None  # None = follows from the targets (default_priority)
    experts_needed: int | None = None  # None = follows from the priority (EXPERTS_FOR_PRIORITY)

    @property
    def effective_priority(self) -> int:
        return self.priority if self.priority is not None else default_priority(list(self.targets.values()))

    @property
    def effective_experts(self) -> int:
        return self.experts_needed if self.experts_needed is not None else EXPERTS_FOR_PRIORITY[self.effective_priority]


@dataclass(frozen=True)
class Certification:
    name: str
    description: str
    valid_months: int | None  # None = never expires
    roles: list[str]  # role names that require it


@dataclass(frozen=True)
class Catalogue:
    roles: list[str]  # display order
    skills: list[Skill]  # display order, each category's skills together
    labels: dict[str, dict[int, str]]  # scale -> level -> text; only the ones the file sets (else the defaults)
    certifications: list[Certification] = field(default_factory=list)

    @property
    def categories(self) -> list[str]:
        return list(dict.fromkeys(s.category for s in self.skills))


def default_priority(targets: list[int]) -> int:
    """3 = core (mean role target >= 3), 1 = niche (mean < 1.5), else 2. Same rule as the workbook.

    A skill with no targets at all gets 2: nothing says it is core or niche.
    """
    if not targets:
        return 2
    mean = sum(targets) / len(targets)
    return 3 if mean >= 3 else (1 if mean < 1.5 else 2)


def load(path: str | Path) -> Catalogue:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise CatalogueError(f"can't read catalogue {path}: {e.strerror}") from e
    return loads(text, str(path))


def loads(text: str, source: str = "catalogue") -> Catalogue:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise CatalogueError(f"{source}: not valid TOML: {e}") from e
    return _parse(data, source)


def _keys(d, where: str, required: set[str], optional: set[str] = frozenset()) -> None:
    """Catch typos like 'descripton' instead of silently ignoring them."""
    if not isinstance(d, dict):
        raise CatalogueError(f"{where}: expected a table")
    if missing := required - d.keys():
        raise CatalogueError(f"{where}: missing {', '.join(sorted(missing))}")
    if unknown := d.keys() - required - optional:
        raise CatalogueError(f"{where}: unknown key {', '.join(sorted(unknown))}")


def _text(value, where: str, *, blank_ok: bool = False, max_len: int | None = None) -> str:
    if not isinstance(value, str):
        raise CatalogueError(f"{where}: expected text")
    value = value.strip()
    if not value and not blank_ok:
        raise CatalogueError(f"{where}: can't be blank")
    if max_len is not None and len(value) > max_len:
        raise CatalogueError(f"{where}: at most {max_len} characters")
    return value


def _list(value, where: str) -> list:
    if not isinstance(value, list):
        raise CatalogueError(f"{where}: expected a list")
    return value


def _targets(value, where: str, codes: dict[str, str]) -> dict[str, int]:
    """{code: level} checked against the role codes and the scale; returned as {role name: level}."""
    if not isinstance(value, dict):
        raise CatalogueError(f"{where}: expected a table like {{ CODE = 3 }}")
    out = {}
    for code, level in value.items():
        if code not in codes:
            raise CatalogueError(f"{where}: unknown role code {code!r} (known: {', '.join(codes)})")
        if isinstance(level, bool) or level not in LEVELS:
            raise CatalogueError(
                f"{where}: target for {code} must be one of {', '.join(map(str, LEVELS))}, not {level!r}"
            )
        out[codes[code]] = level
    return out


def _parse(data: dict, source: str) -> Catalogue:
    _keys(data, source, {"roles"}, {"categories", "levels", "certifications"})
    codes: dict[str, str] = {}  # code -> role name, in file order
    for i, r in enumerate(_list(data["roles"], f"{source}: roles")):
        where = f"{source}: roles[{i + 1}]"
        _keys(r, where, {"code", "name"})
        code, name = _text(r["code"], f"{where} code"), _text(r["name"], f"{where} name", max_len=120)
        if code in codes:
            raise CatalogueError(f"{where}: role code {code!r} is used twice")
        if name in codes.values():
            raise CatalogueError(f"{where}: role {name!r} is listed twice")
        codes[code] = name
    if not codes:
        raise CatalogueError(f"{source}: needs at least one role (assessments need one)")
    order = list(codes.values())

    skills, categories = [], set()
    for i, c in enumerate(_list(data.get("categories", []), f"{source}: categories")):
        where = f"{source}: categories[{i + 1}]"
        _keys(c, where, {"name"}, {"targets", "skills"})
        category = _text(c["name"], f"{where} name", max_len=120)
        if category in categories:
            raise CatalogueError(f"{where}: category {category!r} is listed twice")
        categories.add(category)
        where = f"{source}: category {category!r}"
        defaults = _targets(c.get("targets", {}), f"{where} targets", codes)
        for s in _list(c.get("skills", []), f"{where} skills"):
            _keys(s, f"{where} skill", {"name"}, {"description", "tag", "targets", "priority", "experts_needed"})
            name = _text(s["name"], f"{where} skill name", max_len=120)
            here = f"{where} skill {name!r}"
            if any(x.name == name for x in skills):
                raise CatalogueError(f"{here}: a skill with this name already exists")
            mine = {**defaults, **_targets(s.get("targets", {}), f"{here} targets", codes)}
            skills.append(
                Skill(
                    category,
                    name,
                    _text(s.get("description", ""), f"{here} description", blank_ok=True, max_len=500),
                    _text(s.get("tag", ""), f"{here} tag", blank_ok=True, max_len=MAX_TAG),
                    {role: mine[role] for role in order if role in mine},
                    _int(s.get("priority"), f"{here} priority", 1, 3),
                    _int(s.get("experts_needed"), f"{here} experts_needed", 0, MAX_EXPERTS),
                )
            )
    return Catalogue(
        order,
        skills,
        _labels(data.get("levels", {}), f"{source}: levels"),
        _certifications(data.get("certifications", []), f"{source}: certifications", codes),
    )


def _int(value, where: str, low: int, high: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise CatalogueError(f"{where}: must be a whole number from {low} to {high}, not {value!r}")
    return value


def _certifications(value, where: str, codes: dict[str, str]) -> list[Certification]:
    """[[certifications]] name = "...", description = "...", valid_months = 24, roles = ["CODE", ...]"""
    out: list[Certification] = []
    for i, c in enumerate(_list(value, where)):
        here = f"{where}[{i + 1}]"
        _keys(c, here, {"name"}, {"description", "valid_months", "roles"})
        name = _text(c["name"], f"{here} name", max_len=120)
        if any(x.name == name for x in out):
            raise CatalogueError(f"{here}: certification {name!r} is listed twice")
        roles = []
        for code in _list(c.get("roles", []), f"{here} roles"):
            if code not in codes:
                raise CatalogueError(f"{here} roles: unknown role code {code!r} (known: {', '.join(codes)})")
            roles.append(codes[code])
        out.append(
            Certification(
                name,
                _text(c.get("description", ""), f"{here} description", blank_ok=True, max_len=500),
                _int(c.get("valid_months"), f"{here} valid_months", 1, 600),
                roles,
            )
        )
    return out


def _labels(value, where: str) -> dict[str, dict[int, str]]:
    """[levels] level = { 0 = "...", 3 = "..." }, desire = { ... }: any subset; the rest keep the defaults."""
    _keys(value, where, set(), set(SCALES))
    out = {}
    if isinstance(value.get("desire"), dict) and "0" in value["desire"]:
        raise CatalogueError(
            f"{where}.desire: desire is numbered 1, 2, 3 now (it was 0, 1, 2): "
            "renumber the texts 0 -> 1, 1 -> 2, 2 -> 3"
        )
    for scale, texts in value.items():
        _keys(texts, f"{where}.{scale}", set(), {str(point) for point in SCALES[scale]})
        out[scale] = {int(k): _text(v, f"{where}.{scale} {k}", max_len=120) for k, v in texts.items()}
    return out


# ---------- writing ----------


def _str(value: str) -> str:
    # JSON's string escapes are valid TOML basic strings, except that TOML also forbids a raw DEL
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _role_codes(roles: list[str]) -> dict[str, str]:
    """Role name -> a short ASCII code (TOML keys), from the initials: 'Front-end Developer' -> 'FED'."""
    codes: dict[str, str] = {}
    for name in roles:
        base = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", name)).upper() or "ROLE"
        code, n = base, 2
        while code in codes.values():
            code, n = f"{base}{n}", n + 1
        codes[name] = code
    return codes


def _table(items: dict[str, object]) -> str:
    return "{ " + ", ".join(f"{k} = {v}" for k, v in items.items()) + " }"


def dumps(catalogue: Catalogue, header: str = "") -> str:
    """The catalogue as TOML that `load` reads back to the same catalogue.

    A role becomes a category default only when every skill in the category has a target for it (a skill can't
    leave out a default); priority and experts needed are written only where they differ from what the targets
    give.
    """
    codes = _role_codes(catalogue.roles)
    out = [f"# {line}".rstrip() for line in header.splitlines()]
    out += ["", "roles = ["] + [f"  {_table({'code': _str(codes[r]), 'name': _str(r)})}," for r in catalogue.roles]
    out.append("]")
    if catalogue.labels:
        out += ["", "[levels]"]
        out += [
            f"{scale} = {_table({str(k): _str(v) for k, v in sorted(texts.items())})}"
            for scale, texts in sorted(catalogue.labels.items(), key=lambda kv: list(SCALES).index(kv[0]))
        ]
    for category in catalogue.categories:
        skills = [s for s in catalogue.skills if s.category == category]
        defaults = {}
        for role in catalogue.roles:
            values = [s.targets.get(role) for s in skills]
            if None not in values:
                defaults[role] = Counter(values).most_common(1)[0][0]
        out += ["", "[[categories]]", f"name = {_str(category)}"]
        if defaults:
            out.append(f"targets = {_table({codes[r]: v for r, v in defaults.items()})}")
        out.append("skills = [")
        for s in skills:
            item = {"name": _str(s.name)}
            if s.tag:
                item["tag"] = _str(s.tag)
            if s.description:
                item["description"] = _str(s.description)
            own = {codes[r]: v for r, v in s.targets.items() if defaults.get(r) != v}
            if own:
                item["targets"] = _table(own)
            if s.effective_priority != default_priority(list(s.targets.values())):
                item["priority"] = str(s.effective_priority)
            if s.effective_experts != EXPERTS_FOR_PRIORITY[s.effective_priority]:
                item["experts_needed"] = str(s.effective_experts)
            out.append(f"  {_table(item)},")
        out.append("]")
    for c in catalogue.certifications:
        out += ["", "[[certifications]]", f"name = {_str(c.name)}"]
        if c.description:
            out.append(f"description = {_str(c.description)}")
        if c.valid_months is not None:
            out.append(f"valid_months = {c.valid_months}")
        if c.roles:
            out.append("roles = [" + ", ".join(_str(codes[r]) for r in c.roles) + "]")
    return "\n".join(out) + "\n"
