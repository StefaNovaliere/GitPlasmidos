"""Design rules as data.

Biology owns the rules; engineering owns the engine that runs them. The whole
point of this module is that adding a rule means adding a YAML file, never
touching :mod:`app.domain.rules.engine`.

Two fields are required that a looser schema would have made optional, and
they are the reason this is worth doing at all:

``evidence``
    A threshold with no citation and no organism is folklore. Six months on,
    nobody can tell whether "5-13 nt" was measured in *E. coli* or in yeast, or
    where it came from. Pydantic refuses the rule instead.

``examples``
    Sequences that must and must not trigger the rule. They run in CI, so a
    biologist can add a rule and find out whether it is self-consistent without
    an engineer reading the biology.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: The alphabet a motif may be written in.
IUPAC = frozenset("ACGTNRYSWKMBDHV")

Severity = Literal["error", "warning", "info"]

#: How much weight the literature puts behind a rule. It caps severity: a
#: heuristic is never allowed to block a merge, because a linter that cries
#: wolf gets switched off and does not come back.
Confidence = Literal["established", "reported", "heuristic"]


class Evidence(BaseModel):
    """Where the numbers in a rule came from."""

    model_config = ConfigDict(extra="forbid")

    #: DOI, PMID, or a full reference. Anything a reader can follow.
    citation: str = Field(min_length=8)
    #: The host the measurement was made in. Thresholds are not portable.
    organism: str = Field(min_length=2)
    confidence: Confidence
    notes: str = ""


class Target(BaseModel):
    """Which features the rule runs against."""

    model_config = ConfigDict(extra="forbid")

    feature_kind: str = "CDS"
    strand: Literal["any", "plus", "minus"] = "any"


class Region(BaseModel):
    """Where to look, relative to the target and *in its reading direction*.

    For a feature on the minus strand "upstream" means higher coordinates, not
    lower ones.
    """

    model_config = ConfigDict(extra="forbid")

    where: Literal["upstream", "downstream", "inside"]
    window: int = Field(gt=0, le=100_000, description="bases")


class Look(BaseModel):
    """What to look for: a sequence motif, or an annotated feature."""

    model_config = ConfigDict(extra="forbid")

    motif: str | None = None
    feature_kind: str | None = None
    #: Strand relative to the target feature.
    strand: Literal["same", "opposite", "any"] = "any"

    @field_validator("motif")
    @classmethod
    def _iupac_only(cls, value: str | None) -> str | None:
        if value is None:
            return None
        upper = value.upper()
        bad = sorted(set(upper) - IUPAC)
        if bad:
            raise ValueError(
                "motif has characters outside IUPAC: " + ", ".join(bad)
            )
        return upper

    @model_validator(mode="after")
    def _exactly_one(self) -> Look:
        if bool(self.motif) == bool(self.feature_kind):
            raise ValueError("set exactly one of motif or feature_kind")
        return self


class Expect(BaseModel):
    """What the rule asserts about what it found."""

    model_config = ConfigDict(extra="forbid")

    presence: Literal["required", "forbidden"] = "required"
    #: Gap, in bases, between the hit and the target's edge. Only meaningful
    #: when something is required.
    distance_min: int | None = Field(default=None, ge=0)
    distance_max: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _coherent(self) -> Expect:
        if self.presence == "forbidden" and (
            self.distance_min is not None or self.distance_max is not None
        ):
            raise ValueError("a forbidden hit has no distance to constrain")
        if (
            self.distance_min is not None
            and self.distance_max is not None
            and self.distance_min > self.distance_max
        ):
            raise ValueError("distance_min is greater than distance_max")
        return self


class Example(BaseModel):
    """A case the rule must get right. Run as a test."""

    model_config = ConfigDict(extra="forbid")

    name: str
    sequence: str
    #: Features to annotate on the example, as `kind:start-end[:strand]`.
    #: Coordinates are 0-based, end exclusive - the same as everywhere else.
    features: list[str] = Field(default_factory=list)
    is_circular: bool = False
    #: Whether the rule is expected to report something.
    triggers: bool

    @field_validator("sequence")
    @classmethod
    def _dna(cls, value: str) -> str:
        upper = value.strip().upper()
        bad = sorted(set(upper) - IUPAC)
        if bad:
            raise ValueError("sequence has non-IUPAC characters: " + ", ".join(bad))
        return upper


class Rule(BaseModel):
    """One design rule."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    title: str = Field(min_length=8)
    severity: Severity
    target: Target
    region: Region
    look: Look
    expect: Expect
    #: Rendered with {feature}, {distance}, {motif} and {window}.
    message: str
    evidence: Evidence
    examples: list[Example] = Field(min_length=1)

    @model_validator(mode="after")
    def _confidence_caps_severity(self) -> Rule:
        if self.evidence.confidence == "heuristic" and self.severity == "error":
            raise ValueError(
                "a heuristic rule cannot be an error: it would block a merge on "
                "a number nobody has measured. Use warning or info."
            )
        return self


class Finding(BaseModel):
    """One thing a rule reported about a construct.

    Deliberately the same shape as the reading-frame issues already reported,
    so the UI renders one kind of thing.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    title: str
    severity: Severity
    feature_id: str | None = None
    feature_name: str = ""
    message: str
    #: Genomic span the finding is about, when there is one to point at.
    start: int | None = None
    end: int | None = None
    evidence: Evidence

    @property
    def blocking(self) -> bool:
        return self.severity == "error"


class RuleSet(BaseModel):
    """A loaded pack, plus whatever failed to load.

    Errors are carried rather than raised: one malformed file must not take the
    application down, and the biologist who wrote it needs to see why.
    """

    model_config = ConfigDict(extra="forbid")

    version: str = "unversioned"
    rules: list[Rule] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
