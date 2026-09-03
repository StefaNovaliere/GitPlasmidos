"""HTTP surface for constructs, their operation log and their analyses."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    BranchCreate,
    BranchSummary,
    ConflictOut,
    ConstructCreate,
    ConstructDetail,
    ConstructDiffOut,
    ConstructSummary,
    DiffSide,
    EnzymeSiteOut,
    EnzymesOut,
    FeatureChangeOut,
    FeatureDiffOut,
    FrameIssueOut,
    HistoryOut,
    ImportResult,
    MergePreview,
    MergeRequest,
    MergeSuppression,
    OperationCreate,
    OperationOut,
    OperationsDiffOut,
    OrfOut,
    OrfsOut,
    SequenceDiffOut,
    SequenceSegmentOut,
)
from app.db.models import Construct, OperationRow, utcnow
from app.db.session import get_db
from app.domain.analysis import (
    check_reading_frames,
    find_orfs,
    find_restriction_sites,
)
from app.domain.diff import diff_states
from app.domain.merge import merge_logs
from app.domain.models import (
    ConstructState,
    Feature,
    Operation,
    OperationError,
    SequenceError,
    validate_sequence,
)
from app.domain.replay import apply_operation as apply_domain_operation
from app.domain.replay import replay, validate_feature_bounds
from app.domain.rules import lint, load_rules, suppression_payload
from app.domain.seqio import (
    ImportError_,
    export_fasta,
    export_genbank,
    parse_sequence_file,
)

router = APIRouter(prefix="/api/constructs", tags=["constructs"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

#: The rule pack, read once at import. Rules are data, but they are data that
#: changes at deploy time, not per request.
RULES = load_rules()

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


def _frame_issue(issue) -> FrameIssueOut:
    return FrameIssueOut(
        feature_id=issue.feature_id,
        feature_name=issue.feature_name,
        problem=issue.problem,
        severity=issue.severity,
        detail=issue.detail,
        codon=issue.codon,
        stop_start=issue.stop_start,
        stop_end=issue.stop_end,
        translated_start=issue.translated_start,
        translated_end=issue.translated_end,
        blocking=issue.blocking,
    )


def _detail(construct: Construct, state: ConstructState | None = None) -> dict:
    state = state if state is not None else _derive(construct)
    can_undo, can_redo = _undo_redo_flags(construct)
    return {
        "id": construct.id,
        "name": construct.name,
        "description": construct.description or "",
        "parent_id": construct.parent_id,
        "is_circular": construct.is_circular,
        "sequence": state.sequence,
        "features": state.features,
        "length": state.length,
        "gc_content": state.gc_content,
        "warnings": state.warnings,
        "frame_issues": [_frame_issue(i) for i in check_reading_frames(state)],
        "findings": lint(RULES.rules, state),
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
        description=body.description.strip(),
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
        # GenBank's DEFINITION line, which we used to throw away.
        description=record.description,
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
                "description": construct.description or "",
                "parent_id": construct.parent_id,
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


# ---------------------------------------------------------------------------
# branching and merging
# ---------------------------------------------------------------------------

def _live(construct: Construct) -> list[OperationRow]:
    return [r for r in construct.operations if not r.reverted]


def _merged_boundary(branch: Construct) -> int:
    """Index into the branch's live log up to which its parent is caught up."""
    fork = branch.fork_index or 0
    return min(fork + (branch.merged_ops or 0), len(_live(branch)))


def _ahead(branch: Construct) -> int:
    """Operations the branch has that its parent does not."""
    return max(0, len(_live(branch)) - _merged_boundary(branch))


@router.post(
    "/{construct_id}/branch",
    response_model=ConstructDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_branch(construct_id: str, body: BranchCreate, db: DbSession) -> dict:
    """Fork a construct.

    The fork copies the base and the parent's live operations, and remembers
    how many of them it took. That count is the common ancestor a later merge
    rebases against.
    """
    parent = _load(db, construct_id)
    live = _live(parent)

    branch = Construct(
        id=_new_id(),
        name=(body.name.strip() or f"{parent.name} (branch)")[:255],
        description=body.description.strip(),
        is_circular=parent.is_circular,
        base_sequence=parent.base_sequence,
        base_features=list(parent.base_features or []),
        parent_id=parent.id,
        fork_index=len(live),
    )
    db.add(branch)
    db.flush()
    for i, row in enumerate(live):
        db.add(
            OperationRow(
                id=_new_id(),
                construct_id=branch.id,
                index=i,
                kind=row.kind,
                payload=row.payload,
                reverted=False,
            )
        )
    db.commit()
    db.refresh(branch)
    return _detail(branch)


@router.get("/{construct_id}/branches", response_model=list[BranchSummary])
def list_branches(construct_id: str, db: DbSession) -> list[dict]:
    construct = _load(db, construct_id)
    out = []
    for branch in sorted(construct.branches, key=lambda b: b.created_at):
        state = _derive(branch)
        out.append(
            {
                "id": branch.id,
                "name": branch.name,
                "length": state.length,
                "ahead": _ahead(branch),
                "created_at": branch.created_at,
                "updated_at": branch.updated_at,
            }
        )
    return out


def _preview(branch_id: str, result) -> dict:
    """A JSON-safe summary of a merge.

    Plain primitives rather than models: this doubles as an ``HTTPException``
    detail, and FastAPI serialises those with ``json.dumps``.
    """

    def to_out(conflict) -> ConflictOut:
        return ConflictOut(
            branch_index=conflict.branch_index,
            kind=conflict.kind,
            reason=conflict.reason,
            detail=conflict.detail,
        )

    return MergePreview(
        branch_id=branch_id,
        clean=result.clean,
        rebased=len(result.rebased),
        skipped=[to_out(c) for c in result.skipped],
        conflicts=[to_out(c) for c in result.conflicts],
        new_frame_issues=[_frame_issue(i) for i in result.new_frame_issues],
        new_findings=result.new_findings,
        merged_sequence=(
            result.merged_state.sequence if result.merged_state else None
        ),
        merged_length=(
            result.merged_state.length if result.merged_state else None
        ),
        merged_features=(
            result.merged_state.features if result.merged_state else []
        ),
    ).model_dump(mode="json")


def _prepare_merge(target: Construct, branch: Construct):
    """Validate the pair shares an ancestor, then rebase the branch's log."""
    if branch.parent_id != target.id:
        raise HTTPException(
            HTTP_422_UNPROCESSABLE,
            f"{branch.name!r} is not a branch of {target.name!r}.",
        )
    if branch.base_sequence != target.base_sequence:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The two constructs no longer share a base sequence.",
        )

    fork = branch.fork_index or 0
    target_live, branch_live = _live(target), _live(branch)
    if len(target_live) < fork or len(branch_live) < fork:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "History was undone below the fork point; the two logs no longer "
            "share the ancestor they were forked from.",
        )

    base_features = _base_features(target)
    ancestor_ops = _domain_ops(target_live[:fork])
    shared = replay(
        target.base_sequence, base_features, ancestor_ops,
        is_circular=target.is_circular,
    )
    forked_from = replay(
        target.base_sequence, base_features, _domain_ops(branch_live[:fork]),
        is_circular=target.is_circular,
    )
    if (shared.sequence, shared.features) != (
        forked_from.sequence,
        forked_from.features,
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The shared history diverged after the fork; rebase the branch "
            "manually before merging.",
        )

    return merge_logs(
        target.base_sequence,
        base_features,
        ancestor_ops,
        _domain_ops(target_live[fork:]),
        # Skip what a previous merge already carried across, or the branch's
        # edits get replayed on top of themselves.
        _domain_ops(branch_live[_merged_boundary(branch):]),
        is_circular=target.is_circular,
        construct_id=target.id,
        rules=RULES.rules,
    )


@router.post("/{construct_id}/merge/preview", response_model=MergePreview)
def preview_merge(construct_id: str, body: MergeRequest, db: DbSession) -> dict:
    """Report what merging a branch would do, without writing anything."""
    target = _load(db, construct_id)
    branch = _load(db, body.branch_id)
    return _preview(branch.id, _prepare_merge(target, branch))


def _suppression_ops(
    result,
    requests: list[MergeSuppression],
    *,
    construct_id: str,
    next_index: int,
) -> list[Operation]:
    """Turn "merge anyway, and here is why" into operations.

    The design-rule gate has exactly one door, and going through it signs the
    visitors' book: the reason lands in the merge commit as a
    ``suppress_finding``, against the evidence the engine read in the *merged*
    state — which is the only place the finding exists, since neither tip has
    it on its own.
    """
    by_key = {(f.rule_id, f.feature_id): f for f in result.new_findings}
    out: list[Operation] = []
    for offset, request in enumerate(requests):
        finding = by_key.get((request.rule_id, request.feature_id))
        if finding is None:
            raise HTTPException(
                HTTP_422_UNPROCESSABLE,
                f"{request.rule_id!r} on {request.feature_id!r} is not "
                "blocking this merge; there is nothing to suppress.",
            )
        try:
            payload = suppression_payload(finding, request.reason)
        except ValueError as exc:
            raise HTTPException(HTTP_422_UNPROCESSABLE, str(exc)) from exc
        out.append(
            Operation(
                id=_new_id(),
                construct_id=construct_id,
                index=next_index + offset,
                kind="suppress_finding",
                payload=payload,
            )
        )
    return out


def _still_blocking(result, extra: list[Operation]) -> list:
    """Re-lint the merged state with the suppressions applied.

    Trusting the payload would be enough - it was built from this very state -
    but the whole gate rests on this list, so it is recomputed rather than
    reasoned about.
    """
    if result.merged_state is None:
        return list(result.new_findings)
    merged = result.merged_state.model_copy(deep=True)
    introduced = {(f.rule_id, f.feature_id) for f in result.new_findings}
    for op in extra:
        try:
            apply_domain_operation(merged, op)
        except OperationError as exc:
            raise HTTPException(HTTP_422_UNPROCESSABLE, str(exc)) from exc
    return [
        f
        for f in lint(RULES.rules, merged)
        if f.blocking and (f.rule_id, f.feature_id) in introduced
    ]


@router.post("/{construct_id}/merge", response_model=ConstructDetail)
def merge_branch(construct_id: str, body: MergeRequest, db: DbSession) -> dict:
    """Merge a branch into this construct by rebasing its operations.

    Refuses with 409 when the two logs edited the same bases, when the merge
    introduces reading-frame damage that neither side had, or when it lands the
    construct on a design-rule error that neither side had. The 409 body is a
    :class:`MergePreview` saying which - and for a rule error the finding
    itself is the explanation, down to what the window used to read.
    """
    target = _load(db, construct_id)
    branch = _load(db, body.branch_id)
    result = _prepare_merge(target, branch)

    if result.has_conflicts or (
        result.breaks_biology and not body.allow_frame_breaks
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT, _preview(branch.id, result)
        )

    extra = _suppression_ops(
        result,
        body.suppress,
        construct_id=target.id,
        next_index=(
            result.rebased[-1].index + 1 if result.rebased else len(_live(target))
        ),
    )
    remaining = _still_blocking(result, extra)
    if remaining:
        result.new_findings = remaining
        raise HTTPException(
            status.HTTP_409_CONFLICT, _preview(branch.id, result)
        )

    # Same rule as applying any new operation: a merge discards the redo stack.
    for row in target.operations:
        if row.reverted:
            db.delete(row)
    db.flush()
    for op in result.rebased + extra:
        db.add(
            OperationRow(
                id=_new_id(),  # the branch still owns the original row
                construct_id=target.id,
                index=op.index,
                kind=op.kind,
                payload=op.payload,
                reverted=False,
            )
        )
    branch.merged_ops = len(_live(branch)) - (branch.fork_index or 0)
    target.updated_at = utcnow()
    db.commit()
    db.refresh(target)
    return _detail(target)


# ---------------------------------------------------------------------------
# diffing two constructs
# ---------------------------------------------------------------------------

def _operation_out(row: OperationRow) -> OperationOut:
    return OperationOut(
        id=row.id,
        index=row.index,
        kind=row.kind,
        payload=row.payload or {},
        reverted=row.reverted,
        created_at=row.created_at,
    )


def _operations_diff(left: Construct, right: Construct) -> OperationsDiffOut:
    """Split the two logs at the fork they share, when they share one."""
    left_live, right_live = _live(left), _live(right)
    if right.parent_id == left.id:
        fork = right.fork_index or 0
    elif left.parent_id == right.id:
        fork = left.fork_index or 0
    else:
        fork = 0
    fork = min(fork, len(left_live), len(right_live))
    return OperationsDiffOut(
        shared=fork,
        left_only=[_operation_out(r) for r in left_live[fork:]],
        right_only=[_operation_out(r) for r in right_live[fork:]],
    )


def _feature_change(change) -> FeatureChangeOut:
    return FeatureChangeOut(
        before=change.before,
        after=change.after,
        changed_fields=change.changed_fields,
    )


def _relationship(left: Construct, right: Construct) -> str:
    if right.parent_id == left.id:
        return "branch"
    if left.parent_id == right.id:
        return "parent"
    return "unrelated"


@router.get("/{construct_id}/diff", response_model=ConstructDiffOut)
def diff_constructs(
    construct_id: str,
    db: DbSession,
    against: Annotated[str, Query(description="The construct to compare with")],
) -> dict:
    """Compare this construct's derived state with another's.

    Works on any pair, related or not. When the two are a branch and its
    parent, the operation logs are also split at the fork they share.
    """
    left = _load(db, construct_id)
    right = _load(db, against)
    if left.id == right.id:
        raise HTTPException(
            HTTP_422_UNPROCESSABLE, "A construct cannot be diffed against itself."
        )

    left_state, right_state = _derive(left), _derive(right)
    diff = diff_states(left_state, right_state)

    return {
        "left": DiffSide(id=left.id, name=left.name, length=left_state.length),
        "right": DiffSide(id=right.id, name=right.name, length=right_state.length),
        "relationship": _relationship(left, right),
        "sequence": SequenceDiffOut(
            identical=diff.sequence.identical,
            identity=diff.sequence.identity,
            bases_added=diff.sequence.bases_added,
            bases_removed=diff.sequence.bases_removed,
            origin_shift=diff.sequence.origin_shift,
            segments=[
                SequenceSegmentOut(**vars(segment))
                for segment in diff.sequence.segments
            ],
        ),
        "features": FeatureDiffOut(
            added=diff.features.added,
            removed=diff.features.removed,
            changed=[_feature_change(c) for c in diff.features.changed],
            shifted=[_feature_change(c) for c in diff.features.shifted],
            unchanged=diff.features.unchanged,
        ),
        "operations": _operations_diff(left, right),
    }
