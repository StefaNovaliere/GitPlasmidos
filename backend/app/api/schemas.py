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
