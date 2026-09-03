"""Pure domain models.

No I/O, no FastAPI, no SQLAlchemy. Everything in here is a plain value object
so that :mod:`app.domain.replay` can be tested in isolation.

Coordinate convention
---------------------
All coordinates are 0-based, ``start`` inclusive and ``end`` exclusive, exactly
like Python slices. On a *circular* construct a feature may cross the origin, in
which case ``start > end`` (e.g. ``start=4900, end=120`` on a 5000 bp plasmid).
The degenerate ``start == end`` on a circular construct means "the whole
molecule"; zero-length features are rejected at validation time.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Unambiguous bases plus N, plus the IUPAC ambiguity codes.
IUPAC_ALPHABET = frozenset("ACGTNRYSWKMBDHV")

#: Operation kinds understood by :func:`app.domain.replay.replay`.
OperationKind = Literal[
    "insert",
    "delete",
    "replace",
    "revcomp_region",
    "add_feature",
    "remove_feature",
    "update_feature",
    "set_origin",
    "suppress_finding",
    "unsuppress_finding",
]

Strand = Annotated[int, Field(ge=-1, le=1)]


class SequenceError(ValueError):
    """Raised when a sequence contains characters outside the IUPAC alphabet."""


class OperationError(ValueError):
    """Raised when an operation payload is malformed or out of bounds."""


def validate_sequence(seq: str) -> str:
    """Uppercase ``seq`` and assert it only holds IUPAC nucleotide codes."""
    up = seq.strip().upper()
    bad = sorted(set(up) - IUPAC_ALPHABET)
    if bad:
        raise SequenceError(
            "Invalid nucleotide character(s): "
            + ", ".join(repr(c) for c in bad)
            + f". Allowed alphabet: {''.join(sorted(IUPAC_ALPHABET))}."
        )
    return up


class Feature(BaseModel):
    """An annotated interval on a construct."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    kind: str = "misc_feature"
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    strand: int = 1
    color: str | None = None
    #: Set by the replay engine when an edit clipped this feature.
    truncated: bool = False

    @field_validator("strand")
    @classmethod
    def _check_strand(cls, v: int) -> int:
        if v not in (1, -1):
            raise ValueError("strand must be 1 or -1")
        return v

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, v: str) -> str:
        # Deliberately permissive: GenBank files in the wild carry dozens of
        # feature keys (source, rep_origin, protein_bind, ...) and rejecting
        # them would break import of real plasmids such as pUC19.
        return v.strip() or "misc_feature"


class EvidenceWindow(BaseModel):
    """The bases a rule read, hashed, so a suppression can notice they changed.

    ``digest`` covers the window's *text*, read in the target feature's own
    direction, and nothing else. Coordinates are deliberately not in it: an
    insertion a thousand bases upstream moves this window without changing one
    base of what the rule looked at, and a suppression that died of that would
    teach people to stop reading the linter.

    ``start`` / ``end`` are provenance, not identity. They are carried across
    edits with the same arithmetic features use, so "suppressed on the window
    at 412..433" keeps pointing at those bases. When an edit erases the window
    outright they go to ``None``; the digest will not match either, so the
    finding comes back marked stale, which is the honest answer.
    """

    model_config = ConfigDict(extra="forbid")

    algo: Literal["sha256/1"] = "sha256/1"
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    #: A readable piece of the window, for "it used to read X, now it reads Y".
    excerpt: str = ""
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)


class Suppression(BaseModel):
    """A finding somebody decided not to act on, and why.

    Keyed on ``(rule_id, feature_id)`` — identity, never position. Both sides
    of that key survive every coordinate edit for free, because the feature id
    is already the thing the rest of the log tracks; that is what keeps a
    suppression attached to its finding across an indel upstream.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    feature_id: str
    #: Required. A silenced alarm with no reason is unreadable six months on,
    #: which is exactly when somebody needs to know whether it was deliberate.
    reason: str
    window: EvidenceWindow
    #: Hash of what the rule asserted at suppression time. A rule that changed
    #: its window or its motif is asking a different question.
    rule_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    #: Hash of the whole pack at suppression time. Not used to invalidate
    #: anything - that is the rule digest's job - but it is what lets the UI
    #: separate "you changed the DNA" from "the rules moved underneath you".
    pack_digest: str = ""

    @field_validator("reason")
    @classmethod
    def _needs_a_reason(cls, v: str) -> str:
        text = v.strip()
        if len(text) < 3:
            raise ValueError(
                "a suppression needs a reason: whoever reads this in six "
                "months has only this line to tell a deliberate choice from "
                "an alarm somebody switched off"
            )
        return text


class Operation(BaseModel):
    """One append-only edit. ``reverted`` implements soft undo."""

    model_config = ConfigDict(extra="forbid")

    id: str
    construct_id: str
    index: int = Field(ge=0)
    kind: OperationKind
    payload: dict = Field(default_factory=dict)
    reverted: bool = False


class ConstructState(BaseModel):
    """Derived state: the result of replaying operations over the base."""

    model_config = ConfigDict(extra="forbid")

    sequence: str
    features: list[Feature] = Field(default_factory=list)
    is_circular: bool = True
    warnings: list[str] = Field(default_factory=list)
    #: Findings the log says not to shout about. Derived like everything else:
    #: they come from ``suppress_finding`` operations, not from a stored field.
    suppressions: list[Suppression] = Field(default_factory=list)

    @property
    def length(self) -> int:
        return len(self.sequence)

    @property
    def gc_content(self) -> float:
        """Fraction of G/C over the *unambiguous* bases (0.0 for an empty seq)."""
        if not self.sequence:
            return 0.0
        gc = self.sequence.count("G") + self.sequence.count("C")
        return gc / len(self.sequence)
