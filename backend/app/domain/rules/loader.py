"""Reading a rule pack off disk.

Malformed files are collected, never raised. One bad rule must not take the
application down, and whoever wrote it needs to read why it was rejected.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.domain.models import ConstructState, Feature
from app.domain.rules.models import Example, Rule, RuleSet

#: Where the shipped pack lives.
DEFAULT_RULES_DIR = Path(__file__).resolve().parents[3] / "rules"

_FEATURE = re.compile(
    r"^(?P<kind>[A-Za-z_][\w']*)"
    r":(?P<start>\d+)-(?P<end>\d+)"
    r"(?::(?P<strand>[+-]))?"
    r"(?::(?P<name>[^:]+))?$"
)


def _describe(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in error.errors()
    )


def load_rules(directory: Path | None = None) -> RuleSet:
    """Load every ``*.yaml`` in ``directory``, reporting what would not parse."""
    directory = directory or DEFAULT_RULES_DIR
    ruleset = RuleSet(version=directory.name)
    if not directory.is_dir():
        ruleset.errors.append(f"no rules directory at {directory}")
        return ruleset

    seen: dict[str, Path] = {}
    for path in sorted(directory.glob("*.y*ml")):
        try:
            raw = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            ruleset.errors.append(f"{path.name}: not valid YAML ({exc})")
            continue
        try:
            rule = Rule.model_validate(raw)
        except ValidationError as exc:
            ruleset.errors.append(f"{path.name}: {_describe(exc)}")
            continue
        if rule.id in seen:
            ruleset.errors.append(
                f"{path.name}: id {rule.id!r} already used by {seen[rule.id].name}"
            )
            continue
        seen[rule.id] = path
        ruleset.rules.append(rule)
    return ruleset


def example_state(example: Example) -> ConstructState:
    """Turn an example's shorthand annotations into a state to lint."""
    features = []
    for index, spec in enumerate(example.features):
        match = _FEATURE.match(spec)
        if not match:
            raise ValueError(
                f"cannot read feature {spec!r}; expected kind:start-end[:+/-][:name]"
            )
        features.append(
            Feature(
                id=f"ex{index}",
                name=match["name"] or match["kind"],
                kind=match["kind"],
                start=int(match["start"]),
                end=int(match["end"]),
                strand=-1 if match["strand"] == "-" else 1,
            )
        )
    return ConstructState(
        sequence=example.sequence,
        features=features,
        is_circular=example.is_circular,
    )
