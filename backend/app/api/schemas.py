"""Request and response bodies for the HTTP API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import Feature, OperationKind


class ConstructCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Untitled construct", max_length=255)
    sequence: str = ""
    is_circular: bool = True
    features: list[Feature] = Field(default_factory=list)


class ConstructSummary(BaseModel):
    id: str
    name: str
    parent_id: str | None = None
    is_circular: bool
    length: int
    operation_count: int
    created_at: datetime
    updated_at: datetime


class FrameIssueOut(BaseModel):
    """A coding feature whose reading frame no longer makes a protein."""

    feature_id: str
    feature_name: str
    problem: str
    severity: str
    detail: str
    codon: int | None = None
    #: True for problems severe enough to gate a future branch merge.
    blocking: bool


class ConstructDetail(BaseModel):
    id: str
    name: str
    parent_id: str | None = None
    is_circular: bool
    sequence: str
    features: list[Feature]
    length: int
    gc_content: float
    warnings: list[str]
    frame_issues: list[FrameIssueOut]
    can_undo: bool
    can_redo: bool
    created_at: datetime
    updated_at: datetime


class ImportResult(ConstructDetail):
    """A freshly imported construct plus whatever the parser complained about."""

    import_warnings: list[str] = Field(default_factory=list)


class OperationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: OperationKind
    payload: dict = Field(default_factory=dict)


class OperationOut(BaseModel):
    id: str
    index: int
    kind: str
    payload: dict
    reverted: bool
    created_at: datetime


class HistoryOut(BaseModel):
    construct_id: str
    operations: list[OperationOut]
    can_undo: bool
    can_redo: bool


class EnzymeSiteOut(BaseModel):
    name: str
    site: str
    cut_positions: list[int]
    cuts: int
    overhang: str


class EnzymesOut(BaseModel):
    construct_id: str
    length: int
    is_circular: bool
    enzymes: list[EnzymeSiteOut]


class OrfOut(BaseModel):
    start: int
    end: int
    strand: int
    length: int
    frame: int
    protein: str


class OrfsOut(BaseModel):
    construct_id: str
    min_length: int
    orfs: list[OrfOut]


class BranchCreate(BaseModel):
    """Fork a construct, carrying its base and its live operations."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=255)


class BranchSummary(BaseModel):
    id: str
    name: str
    length: int
    #: Operations this branch has added since the fork.
    ahead: int
    created_at: datetime
    updated_at: datetime


class ConflictOut(BaseModel):
    """One branch operation that could not be replayed onto the target."""

    branch_index: int
    kind: str
    reason: str
    detail: str


class MergePreview(BaseModel):
    """What a merge would do. Also the body of a 409 when it cannot proceed."""

    branch_id: str
    clean: bool
    #: Operations that would be appended to the target's log.
    rebased: int
    #: Branch operations the target had already satisfied.
    skipped: list[ConflictOut] = Field(default_factory=list)
    conflicts: list[ConflictOut] = Field(default_factory=list)
    #: Reading-frame damage the merge itself introduces.
    new_frame_issues: list[FrameIssueOut] = Field(default_factory=list)


class MergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branch_id: str
    #: Commit even though the merge breaks a reading frame. Off by default:
    #: silently shipping a dead protein is the failure this project exists to
    #: prevent, but deliberately building a frameshift mutant is real work.
    allow_frame_breaks: bool = False


class DiffSide(BaseModel):
    id: str
    name: str
    length: int


class SequenceSegmentOut(BaseModel):
    """One block of the alignment: equal, insert, delete or replace."""

    op: str
    left_start: int
    left_end: int
    right_start: int
    right_end: int
    #: Only carried for changed blocks, and capped.
    left_seq: str = ""
    right_seq: str = ""
    truncated: bool = False


class SequenceDiffOut(BaseModel):
    identical: bool
    identity: float
    bases_added: int
    bases_removed: int
    #: How far the right molecule was rotated to line its origin up with the
    #: left's. Non-zero means somebody ran set_origin on one side.
    origin_shift: int
    segments: list[SequenceSegmentOut]


class FeatureChangeOut(BaseModel):
    before: Feature
    after: Feature
    changed_fields: list[str]


class FeatureDiffOut(BaseModel):
    added: list[Feature] = Field(default_factory=list)
    removed: list[Feature] = Field(default_factory=list)
    #: Genuinely different: renamed, restranded, or over different bases.
    changed: list[FeatureChangeOut] = Field(default_factory=list)
    #: Merely displaced by an indel elsewhere, still over the same bases.
    shifted: list[FeatureChangeOut] = Field(default_factory=list)
    unchanged: int = 0


class OperationsDiffOut(BaseModel):
    #: Operations both sides inherited from their common ancestor.
    shared: int
    left_only: list[OperationOut] = Field(default_factory=list)
    right_only: list[OperationOut] = Field(default_factory=list)


class ConstructDiffOut(BaseModel):
    left: DiffSide
    right: DiffSide
    #: branch | parent | unrelated, from the left's point of view.
    relationship: str
    sequence: SequenceDiffOut
    features: FeatureDiffOut
    operations: OperationsDiffOut
