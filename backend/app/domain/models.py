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
