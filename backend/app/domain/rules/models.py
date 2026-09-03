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

from app.domain.models import EvidenceWindow

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
    #: Used when the rule finds nothing at all. "There is no Shine-Dalgarno
    #: here" and "there is one, 22 nt away" are two different diagnoses and a
    #: biologist acts on them differently; one template cannot say both, and
    #: trying leaves a {distance} with no value to put in it.
    message_missing: str = ""
    evidence: Evidence
    examples: list[Example] = Field(min_length=1)

    @model_validator(mode="after")
    def _absence_reads_as_an_absence(self) -> Rule:
        can_be_absent = self.expect.presence == "required"
        if not can_be_absent and self.message_missing:
            raise ValueError(
                "a forbidden hit has no absence to report; drop message_missing"
            )
        if "{distance}" in self.message_missing:
            raise ValueError(
                "message_missing describes finding nothing, which has no "
                "distance; drop {distance} from it"
            )
        if can_be_absent and "{distance}" in self.message and not self.message_missing:
            raise ValueError(
                "this rule can report that it found nothing, and its message "
                "interpolates {distance}, which has no value in that case. Add "
                "message_missing: an absent motif and one at the wrong spacing "
                "are two different diagnoses."
            )
        return self

    @model_validator(mode="after")
    def _confidence_caps_severity(self) -> Rule:
        if self.evidence.confidence == "heuristic" and self.severity == "error":
            raise ValueError(
                "a heuristic rule cannot be an error: it would block a merge on "
                "a number nobody has measured. Use warning or info."
            )
        return self


class SuppressionState(BaseModel):
    """Why a finding is quiet, or why it started talking again.

    A suppression never disappears silently and never persists silently. When
    the evidence under it changed, the finding comes back carrying both
    readings, and the decision goes back to whoever made it.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str
    #: True when the bases the rule reads, or the rule itself, changed since.
    stale: bool = False
    changed: Literal["evidence", "rule"] | None = None
    #: What the window read when it was suppressed, and what it reads now.
    was: str = ""
    now: str = ""


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
    #: The bases the rule actually read, hashed. Sent back verbatim in a
    #: ``suppress_finding`` operation: the engine knows what it looked at, the
    #: client should not have to guess.
    window: EvidenceWindow | None = None
    rule_digest: str = ""
    #: Suppressed findings are marked, never dropped. Hidden ones rot.
    suppressed: bool = False
    suppression: SuppressionState | None = None

    @property
    def blocking(self) -> bool:
        return self.severity == "error" and not self.suppressed


class RuleSet(BaseModel):
    """A loaded pack, plus whatever failed to load.

    Errors are carried rather than raised: one malformed file must not take the
    application down, and the biologist who wrote it needs to see why.
    """

    model_config = ConfigDict(extra="forbid")

    version: str = "unversioned"
    rules: list[Rule] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
