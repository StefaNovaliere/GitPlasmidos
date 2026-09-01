"""The heart of the app: deriving current state by replaying operations.

Nothing is ever stored except ``base_sequence`` / ``base_features`` and the
append-only operation log. :func:`replay` folds the non-reverted operations
over the base, which makes undo/redo a matter of flipping a boolean and keeps
the whole edit history auditable.

This module is pure: no I/O, no FastAPI, no SQLAlchemy.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.domain.circular import (
    recombine,
    revcomp,
    rotate_sequence,
    segments,
    span_length,
)
from app.domain.models import (
    ConstructState,
    Feature,
    Operation,
    OperationError,
    validate_sequence,
)

Segment = tuple[int, int]


# ---------------------------------------------------------------------------
# feature validation
# ---------------------------------------------------------------------------

def validate_feature_bounds(f: Feature, length: int, is_circular: bool) -> None:
    """Assert a feature's coordinates make sense for a construct of ``length``."""
    if length == 0:
        raise OperationError("cannot place a feature on an empty sequence")
    if not (0 <= f.start < length):
        raise OperationError(
            f"feature {f.name!r}: start {f.start} out of range [0, {length})"
        )
    if not (0 <= f.end <= length):
        raise OperationError(
            f"feature {f.name!r}: end {f.end} out of range [0, {length}]"
        )
    if not is_circular and f.start >= f.end:
        raise OperationError(
            f"feature {f.name!r}: start must be < end on a linear construct "
            f"(got {f.start} >= {f.end})"
        )
    if span_length(f.start, f.end, length, is_circular) == 0:
        raise OperationError(f"feature {f.name!r}: zero-length features are not allowed")


def _normalize_range(
    start: int, end: int, length: int, is_circular: bool, what: str
) -> tuple[int, int, bool]:
    """Validate an operation range, reporting whether it crosses the origin."""
    if not (0 <= start <= length) or not (0 <= end <= length):
        raise OperationError(
            f"{what}: range [{start}, {end}) out of bounds for length {length}"
        )
    if start == end:
        raise OperationError(f"{what}: empty range [{start}, {end})")
    if start < end:
        return start, end, False
    if not is_circular:
        raise OperationError(
            f"{what}: start must be < end on a linear construct "
            f"(got {start} >= {end})"
        )
    return start, end, True


# ---------------------------------------------------------------------------
# rebasing rules
# ---------------------------------------------------------------------------

def rebase_insert(
    features: Iterable[Feature],
    pos: int,
    ins_len: int,
    length: int,
    is_circular: bool,
) -> tuple[list[Feature], list[str]]:
    """Shift features across an insertion of ``ins_len`` bases at ``pos``.

    A single endpoint rule covers every case in the spec, wraparound included:

    * ``start`` moves when ``start >= pos`` (the insert lands before it),
    * ``end`` moves when ``end > pos`` (the insert lands before its last base).

    A feature that strictly contains ``pos`` therefore keeps its start and grows
    its end, i.e. it swallows the inserted bases.

    ``length`` and ``is_circular`` are unused here - insertion needs no segment
    decomposition - but are kept so all four ``rebase_*`` helpers share a shape.
    """
    out: list[Feature] = []
    for f in features:
        g = f.model_copy(deep=True)
        if g.start >= pos:
            g.start += ins_len
        if g.end > pos:
            g.end += ins_len
        out.append(g)
    return out, []


def rebase_delete(
    features: Iterable[Feature],
    start: int,
    end: int,
    length: int,
    is_circular: bool,
) -> tuple[list[Feature], list[str]]:
    """Rebase features across a deletion of the **linear** range ``[start, end)``.

    Wrapping ranges never reach this function: :func:`replay` rotates the
    construct first so that the deleted range starts at 0.

    * fully deleted feature  -> dropped, with a warning,
    * partially overlapped   -> clipped to the deletion border, ``truncated``,
    * entirely downstream    -> both endpoints move back by ``end - start``.
    """
    removed = end - start
    new_length = length - removed
    warnings: list[str] = []

    def map_start(x: int) -> int:
        if x < start:
            return x
        if x < end:
            return start
        return x - removed

    def map_end(x: int) -> int:
        if x <= start:
            return x
        if x <= end:
            return start
        return x - removed

    out: list[Feature] = []
    for f in features:
        old_span = span_length(f.start, f.end, length, is_circular)
        segs = segments(f.start, f.end, length, is_circular)
        moved = [(map_start(s), map_end(e)) for s, e in segs]
        joined = recombine(moved, new_length, is_circular)
        if joined is None:
            warnings.append(f"feature {f.name!r} eliminada por delete")
            continue
        g = f.model_copy(deep=True)
        g.start, g.end = joined
        new_span = span_length(g.start, g.end, new_length, is_circular)
        if new_span < old_span:
            g.truncated = True
            warnings.append(
                f"feature {f.name!r} recortada por delete "
                f"({old_span} -> {new_span} pb)"
            )
        out.append(g)
    return out, warnings


def rebase_revcomp(
    features: Iterable[Feature],
    start: int,
    end: int,
    length: int,
    is_circular: bool,
) -> tuple[list[Feature], list[str]]:
    """Mirror features inside the **linear** range ``[start, end)``.

    Contained features flip strand and are mirrored within the range:
    ``new_start = start + (end - old_end)``. Features that only partially
    overlap the range are left untouched and reported as warnings, because
    there is no sensible single interval for them afterwards.
    """
    warnings: list[str] = []
    range_positions = set(range(start, end))
    out: list[Feature] = []
    for f in features:
        segs = segments(f.start, f.end, length, is_circular)
        covered = {p for s, e in segs for p in range(s, e)}
        inside = covered <= range_positions
        if inside:
            g = f.model_copy(deep=True)
            # Mirror the *closed* interval, then convert back to half-open.
            if f.start < f.end:
                g.start = start + (end - f.end)
                g.end = start + (end - f.start)
            else:
                # Only reachable when the range is the whole circle.
                g.start = (start + (end - f.end)) % length
                g.end = (start + (end - f.start)) % length
            g.strand = -g.strand
            out.append(g)
        elif covered & range_positions:
            warnings.append(
                f"feature {f.name!r} cruza el borde del revcomp "
                f"[{start}, {end}) y se dejó sin modificar"
            )
            out.append(f.model_copy(deep=True))
        else:
            out.append(f.model_copy(deep=True))
    return out, warnings


def rebase_rotate(
    features: Iterable[Feature], origin: int, length: int
) -> list[Feature]:
    """Re-express features after ``set_origin(origin)`` rotated the molecule.

    Every coordinate moves by ``-origin`` modulo ``length``. Using the *last
    covered base* rather than the exclusive end keeps full-length and
    origin-crossing features intact.
    """
    out: list[Feature] = []
    origin %= length
    for f in features:
        g = f.model_copy(deep=True)
        g.start = (f.start - origin) % length
        last = (f.end - 1) % length
        g.end = ((last - origin) % length) + 1
        out.append(g)
    return out


# ---------------------------------------------------------------------------
# operation application
# ---------------------------------------------------------------------------

def _require(payload: dict, key: str, what: str):
    if key not in payload:
        raise OperationError(f"{what}: missing payload field {key!r}")
    return payload[key]


def _as_int(value, what: str, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OperationError(f"{what}: {field} must be an integer, got {value!r}")
    return value


def _apply_rotate(state: ConstructState, origin: int) -> None:
    length = len(state.sequence)
    if not state.is_circular:
        raise OperationError("set_origin: only meaningful on a circular construct")
    if length == 0:
        raise OperationError("set_origin: empty sequence")
    if not (0 <= origin < length):
        raise OperationError(
            f"set_origin: pos {origin} out of range [0, {length})"
        )
    if origin == 0:
        return
    state.sequence = rotate_sequence(state.sequence, origin)
    state.features = rebase_rotate(state.features, origin, length)


def _apply_insert(state: ConstructState, pos: int, seq: str) -> None:
    seq = validate_sequence(seq)
    if not seq:
        raise OperationError("insert: empty sequence")
    length = len(state.sequence)
    if pos < 0:
        raise OperationError(f"insert: pos {pos} must be >= 0")
    if pos > length:
        if not state.is_circular or length == 0:
            raise OperationError(
                f"insert: pos {pos} out of range [0, {length}]"
            )
        pos %= length
    state.sequence = state.sequence[:pos] + seq + state.sequence[pos:]
    state.features, warns = rebase_insert(
        state.features, pos, len(seq), length, state.is_circular
    )
    state.warnings.extend(warns)


def _apply_delete(state: ConstructState, start: int, end: int) -> bool:
    """Delete ``[start, end)``. Returns True if the range crossed the origin."""
    length = len(state.sequence)
    start, end, wraps = _normalize_range(
        start, end, length, state.is_circular, "delete"
    )
    if wraps:
        removed = span_length(start, end, length, True)
        if removed >= length:
            raise OperationError("delete: cannot delete the entire sequence")
        _apply_rotate(state, start)
        start, end = 0, removed
    if end - start >= length:
        raise OperationError("delete: cannot delete the entire sequence")
    state.sequence = state.sequence[:start] + state.sequence[end:]
    state.features, warns = rebase_delete(
        state.features, start, end, length, state.is_circular
    )
    state.warnings.extend(warns)
    return wraps


def _apply_revcomp_linear(state: ConstructState, start: int, end: int) -> None:
    length = len(state.sequence)
    state.sequence = (
        state.sequence[:start]
        + revcomp(state.sequence[start:end])
        + state.sequence[end:]
    )
    state.features, warns = rebase_revcomp(
        state.features, start, end, length, state.is_circular
    )
    state.warnings.extend(warns)


def _apply_revcomp(state: ConstructState, start: int, end: int) -> None:
    length = len(state.sequence)
    start, end, wraps = _normalize_range(
        start, end, length, state.is_circular, "revcomp_region"
    )
    if not wraps:
        _apply_revcomp_linear(state, start, end)
        return
    # Rotate the wrapping range to the front, reverse it there, rotate back so
    # that untouched bases keep their original coordinates.
    span = span_length(start, end, length, True)
    _apply_rotate(state, start)
    _apply_revcomp_linear(state, 0, span)
    _apply_rotate(state, (length - start) % length)


def _apply_add_feature(state: ConstructState, raw: dict) -> None:
    try:
        f = Feature.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError
        raise OperationError(f"add_feature: invalid feature ({exc})") from exc
    validate_feature_bounds(f, len(state.sequence), state.is_circular)
    if any(existing.id == f.id for existing in state.features):
        raise OperationError(f"add_feature: duplicate feature id {f.id!r}")
    state.features.append(f)


def _apply_remove_feature(state: ConstructState, feature_id: str) -> None:
    kept = [f for f in state.features if f.id != feature_id]
    if len(kept) == len(state.features):
        raise OperationError(f"remove_feature: no feature with id {feature_id!r}")
    state.features = kept


_PATCHABLE = {"name", "kind", "start", "end", "strand", "color", "truncated"}


def _apply_update_feature(
    state: ConstructState, feature_id: str, patch: dict
) -> None:
    unknown = sorted(set(patch) - _PATCHABLE)
    if unknown:
        raise OperationError(
            "update_feature: unknown field(s) " + ", ".join(repr(k) for k in unknown)
        )
    for i, f in enumerate(state.features):
        if f.id != feature_id:
            continue
        try:
            updated = f.model_copy(update=patch)
            updated = Feature.model_validate(updated.model_dump())
        except Exception as exc:
            raise OperationError(f"update_feature: invalid patch ({exc})") from exc
        validate_feature_bounds(updated, len(state.sequence), state.is_circular)
        state.features[i] = updated
        return
    raise OperationError(f"update_feature: no feature with id {feature_id!r}")


def apply_operation(state: ConstructState, op: Operation) -> None:
    """Apply one operation to ``state`` in place. Raises :class:`OperationError`."""
    p = op.payload
    kind = op.kind
    if kind == "insert":
        _apply_insert(
            state,
            _as_int(_require(p, "pos", "insert"), "insert", "pos"),
            _require(p, "seq", "insert"),
        )
    elif kind == "delete":
        _apply_delete(
            state,
            _as_int(_require(p, "start", "delete"), "delete", "start"),
            _as_int(_require(p, "end", "delete"), "delete", "end"),
        )
    elif kind == "replace":
        start = _as_int(_require(p, "start", "replace"), "replace", "start")
        end = _as_int(_require(p, "end", "replace"), "replace", "end")
        seq = validate_sequence(_require(p, "seq", "replace"))
        if not seq:
            raise OperationError("replace: empty sequence")
        # Deliberately implemented as delete + insert rather than a third path.
        wrapped = _apply_delete(state, start, end)
        _apply_insert(state, 0 if wrapped else start, seq)
    elif kind == "revcomp_region":
        _apply_revcomp(
            state,
            _as_int(_require(p, "start", "revcomp_region"), "revcomp_region", "start"),
            _as_int(_require(p, "end", "revcomp_region"), "revcomp_region", "end"),
        )
    elif kind == "add_feature":
        _apply_add_feature(state, _require(p, "feature", "add_feature"))
    elif kind == "remove_feature":
        _apply_remove_feature(state, _require(p, "feature_id", "remove_feature"))
    elif kind == "update_feature":
        _apply_update_feature(
            state,
            _require(p, "feature_id", "update_feature"),
            _require(p, "patch", "update_feature"),
        )
    elif kind == "set_origin":
        _apply_rotate(
            state, _as_int(_require(p, "pos", "set_origin"), "set_origin", "pos")
        )
    else:  # pragma: no cover - Operation.kind is a Literal
        raise OperationError(f"unknown operation kind {kind!r}")


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def replay(
    base_seq: str,
    base_features: list[Feature],
    ops: list[Operation],
    *,
    is_circular: bool = True,
    strict: bool = False,
) -> ConstructState:
    """Fold the non-reverted operations of ``ops`` over the base sequence.

    Operations are applied in ``index`` order; ``reverted`` ones are skipped.
    With ``strict=False`` (the default, used when serving reads) a broken
    operation is skipped and reported in ``state.warnings`` so that a construct
    can never become unopenable. With ``strict=True`` (used when validating a
    freshly submitted operation) the underlying :class:`OperationError`
    propagates.
    """
    state = ConstructState(
        sequence=validate_sequence(base_seq),
        features=[f.model_copy(deep=True) for f in base_features],
        is_circular=is_circular,
        warnings=[],
    )
    for op in sorted(ops, key=lambda o: o.index):
        if op.reverted:
            continue
        try:
            apply_operation(state, op)
        except OperationError as exc:
            if strict:
                raise
            state.warnings.append(f"operación #{op.index} ({op.kind}) omitida: {exc}")
    return state
