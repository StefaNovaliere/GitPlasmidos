"""HTTP surface for constructs, their operation log and their analyses."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    ConstructCreate,
    ConstructDetail,
    ConstructSummary,
    EnzymesOut,
    EnzymeSiteOut,
    HistoryOut,
    ImportResult,
    OperationCreate,
    OperationOut,
    OrfOut,
    OrfsOut,
)
from app.db.models import Construct, OperationRow, utcnow
from app.db.session import get_db
from app.domain.analysis import find_orfs, find_restriction_sites
from app.domain.models import (
    ConstructState,
    Feature,
    Operation,
    OperationError,
    SequenceError,
    validate_sequence,
)
from app.domain.replay import replay, validate_feature_bounds
from app.domain.seqio import (
    ImportError_,
    export_fasta,
    export_genbank,
    parse_sequence_file,
)

router = APIRouter(prefix="/api/constructs", tags=["constructs"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# Spelled out rather than taken from ``status``: the constant names for these
# two codes were renamed in Starlette 1.6 and the old ones now warn.
HTTP_422_UNPROCESSABLE = 422
HTTP_413_TOO_LARGE = 413


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _new_id() -> str:
    return str(uuid.uuid4())


def _load(db: Session, construct_id: str) -> Construct:
    construct = db.get(Construct, construct_id)
    if construct is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"No construct with id {construct_id!r}"
        )
    return construct


def _base_features(construct: Construct) -> list[Feature]:
    return [Feature.model_validate(f) for f in construct.base_features or []]


def _domain_ops(rows: list[OperationRow]) -> list[Operation]:
    return [
        Operation(
            id=r.id,
            construct_id=r.construct_id,
            index=r.index,
            kind=r.kind,
            payload=r.payload or {},
            reverted=r.reverted,
        )
        for r in rows
    ]


def _derive(construct: Construct, *, extra: Operation | None = None,
            strict: bool = False) -> ConstructState:
    """Replay the log (optionally with a candidate operation appended)."""
    ops = _domain_ops(list(construct.operations))
    if extra is not None:
        ops = [o for o in ops if not o.reverted] + [extra]
    return replay(
        construct.base_sequence,
        _base_features(construct),
        ops,
        is_circular=construct.is_circular,
        strict=strict,
    )


def _undo_redo_flags(construct: Construct) -> tuple[bool, bool]:
    rows = list(construct.operations)
    return (
        any(not r.reverted for r in rows),
        any(r.reverted for r in rows),
    )


def _detail(construct: Construct, state: ConstructState | None = None) -> dict:
    state = state if state is not None else _derive(construct)
    can_undo, can_redo = _undo_redo_flags(construct)
    return {
        "id": construct.id,
        "name": construct.name,
        "is_circular": construct.is_circular,
        "sequence": state.sequence,
        "features": state.features,
        "length": state.length,
        "gc_content": state.gc_content,
        "warnings": state.warnings,
        "can_undo": can_undo,
        "can_redo": can_redo,
        "created_at": construct.created_at,
        "updated_at": construct.updated_at,
    }


DbSession = Annotated[Session, Depends(get_db)]


# ---------------------------------------------------------------------------
# create / list / read
# ---------------------------------------------------------------------------

@router.post("", response_model=ConstructDetail, status_code=status.HTTP_201_CREATED)
def create_construct(body: ConstructCreate, db: DbSession) -> dict:
    """Create an empty construct, or one from a raw sequence."""
    try:
        sequence = validate_sequence(body.sequence)
    except SequenceError as exc:
        raise HTTPException(HTTP_422_UNPROCESSABLE, str(exc)) from exc

    for feature in body.features:
        try:
            validate_feature_bounds(feature, len(sequence), body.is_circular)
        except OperationError as exc:
            raise HTTPException(
                HTTP_422_UNPROCESSABLE, str(exc)
            ) from exc

    construct = Construct(
        id=_new_id(),
        name=body.name.strip() or "Untitled construct",
        is_circular=body.is_circular,
        base_sequence=sequence,
        base_features=[f.model_dump() for f in body.features],
    )
    db.add(construct)
    db.commit()
    db.refresh(construct)
    return _detail(construct)


@router.post(
    "/import", response_model=ImportResult, status_code=status.HTTP_201_CREATED
)
async def import_construct(
    db: DbSession,
    file: Annotated[UploadFile, File(description=".fasta, .fa, .gb or .gbk")],
    name: Annotated[str | None, Query(description="Override the record name")] = None,
) -> dict:
    """Import the first record of an uploaded FASTA or GenBank file."""
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            HTTP_413_TOO_LARGE,
            f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )
    try:
        record = parse_sequence_file(raw, file.filename)
    except ImportError_ as exc:
        raise HTTPException(HTTP_422_UNPROCESSABLE, str(exc)) from exc

    construct = Construct(
        id=_new_id(),
        name=(name or record.name).strip()[:255] or "imported",
        # FASTA carries no topology; a plasmid editor defaults to circular.
        is_circular=True if record.is_circular is None else record.is_circular,
        base_sequence=record.sequence,
        base_features=[f.model_dump() for f in record.features],
    )
    db.add(construct)
    db.commit()
    db.refresh(construct)
    return {**_detail(construct), "import_warnings": record.warnings}


@router.get("", response_model=list[ConstructSummary])
def list_constructs(db: DbSession) -> list[dict]:
    rows = db.scalars(select(Construct).order_by(Construct.updated_at.desc())).all()
    out = []
    for construct in rows:
        # Length changes with the log, so it has to come from the derived state.
        state = _derive(construct)
        out.append(
            {
                "id": construct.id,
                "name": construct.name,
                "is_circular": construct.is_circular,
                "length": state.length,
                "operation_count": len(construct.operations),
                "created_at": construct.created_at,
                "updated_at": construct.updated_at,
            }
        )
    return out


@router.get("/{construct_id}", response_model=ConstructDetail)
def get_construct(construct_id: str, db: DbSession) -> dict:
    return _detail(_load(db, construct_id))


@router.delete("/{construct_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_construct(construct_id: str, db: DbSession) -> Response:
    db.delete(_load(db, construct_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# operations, undo / redo, history
# ---------------------------------------------------------------------------

@router.post(
    "/{construct_id}/operations",
    response_model=ConstructDetail,
    status_code=status.HTTP_201_CREATED,
)
def apply_operation(construct_id: str, body: OperationCreate, db: DbSession) -> dict:
    """Append an operation, dropping any operations that were undone first."""
    construct = _load(db, construct_id)
    rows = list(construct.operations)
    kept = [r for r in rows if not r.reverted]

    candidate = Operation(
        id=_new_id(),
        construct_id=construct.id,
        index=len(kept),
        kind=body.kind,
        payload=body.payload,
    )
    try:
        state = _derive(construct, extra=candidate, strict=True)
    except (OperationError, SequenceError) as exc:
        raise HTTPException(HTTP_422_UNPROCESSABLE, str(exc)) from exc

    # Text-editor semantics: a new edit discards the redo stack for good.
    # The delete has to hit the database before the insert, because the new
    # operation reuses an index the reverted rows still occupy and
    # (construct_id, index) is unique.
    for row in rows:
        if row.reverted:
            db.delete(row)
    db.flush()

    db.add(
        OperationRow(
            id=candidate.id,
            construct_id=construct.id,
            index=candidate.index,
            kind=candidate.kind,
            payload=candidate.payload,
            reverted=False,
        )
    )
    construct.updated_at = utcnow()
    db.commit()
    db.refresh(construct)
    return _detail(construct, state)


@router.post("/{construct_id}/undo", response_model=ConstructDetail)
def undo(construct_id: str, db: DbSession) -> dict:
    construct = _load(db, construct_id)
    live = [r for r in construct.operations if not r.reverted]
    if not live:
        raise HTTPException(status.HTTP_409_CONFLICT, "Nothing to undo.")
    live[-1].reverted = True
    construct.updated_at = utcnow()
    db.commit()
    db.refresh(construct)
    return _detail(construct)


@router.post("/{construct_id}/redo", response_model=ConstructDetail)
def redo(construct_id: str, db: DbSession) -> dict:
    construct = _load(db, construct_id)
    undone = [r for r in construct.operations if r.reverted]
    if not undone:
        raise HTTPException(status.HTTP_409_CONFLICT, "Nothing to redo.")
    undone[0].reverted = False
    construct.updated_at = utcnow()
    db.commit()
    db.refresh(construct)
    return _detail(construct)


@router.get("/{construct_id}/history", response_model=HistoryOut)
def history(construct_id: str, db: DbSession) -> dict:
    construct = _load(db, construct_id)
    can_undo, can_redo = _undo_redo_flags(construct)
    return {
        "construct_id": construct.id,
        "operations": [
            OperationOut(
                id=r.id,
                index=r.index,
                kind=r.kind,
                payload=r.payload or {},
                reverted=r.reverted,
                created_at=r.created_at,
            )
            for r in construct.operations
        ],
        "can_undo": can_undo,
        "can_redo": can_redo,
    }


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

@router.get("/{construct_id}/export")
def export_construct(
    construct_id: str,
    db: DbSession,
    format: Annotated[str, Query(pattern="^(genbank|fasta)$")] = "genbank",
) -> Response:
    """Export the *current* derived state, not the base sequence."""
    construct = _load(db, construct_id)
    state = _derive(construct)
    if format == "genbank":
        body, ext = export_genbank(state, construct.name), "gb"
    else:
        body, ext = export_fasta(state, construct.name), "fasta"
    filename = f"{construct.name.replace(' ', '_')[:60] or 'construct'}.{ext}"
    return Response(
        content=body,
        media_type="chemical/x-genbank" if ext == "gb" else "text/x-fasta",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# analyses
# ---------------------------------------------------------------------------

@router.get("/{construct_id}/enzymes", response_model=EnzymesOut)
def enzymes(
    construct_id: str,
    db: DbSession,
    all: Annotated[
        bool,
        Query(description="Widen to every commercial enzyme and to multi-cutters"),
    ] = False,
    names: Annotated[
        str | None, Query(description="Comma-separated enzyme names")
    ] = None,
) -> dict:
    """Restriction sites; single cutters from the curated cloning set by default."""
    construct = _load(db, construct_id)
    state = _derive(construct)
    wanted = [n.strip() for n in names.split(",") if n.strip()] if names else None
    sites = find_restriction_sites(
        state.sequence,
        state.is_circular,
        single_cutters_only=not all,
        use_all=all,
        names=wanted,
    )
    return {
        "construct_id": construct.id,
        "length": state.length,
        "is_circular": state.is_circular,
        "enzymes": [
            EnzymeSiteOut(
                name=s.name,
                site=s.site,
                cut_positions=s.cut_positions,
                cuts=s.cuts,
                overhang=s.overhang,
            )
            for s in sites
        ],
    }


@router.get("/{construct_id}/orfs", response_model=OrfsOut)
def orfs(
    construct_id: str,
    db: DbSession,
    min_length: Annotated[int, Query(ge=3, le=100_000)] = 300,
) -> dict:
    """ORFs in all six frames, standard genetic code (NCBI table 1)."""
    construct = _load(db, construct_id)
    state = _derive(construct)
    found = find_orfs(state.sequence, state.is_circular, min_length=min_length)
    return {
        "construct_id": construct.id,
        "min_length": min_length,
        "orfs": [
            OrfOut(
                start=o.start,
                end=o.end,
                strand=o.strand,
                length=o.length,
                frame=o.frame,
                protein=o.protein,
            )
            for o in found
        ],
    }
