"""Merging two operation logs.

Because a construct's history is a list of *semantic* operations rather than a
blob of text, merging two branches is a rebase: take the operations the branch
added after the fork, re-express their coordinates as if the target's own new
operations had happened first, and append them.

That is the same endpoint arithmetic :mod:`app.domain.replay` uses to move
features across an edit, turned ninety degrees to move *operations* instead.

Two independent things can go wrong, and they are reported separately:

* **Coordinate conflicts** — the two branches touched the same bases. Detected
  here, exactly as a text merge reports overlapping hunks.
* **Biological breakage** — the merge applies cleanly and still ruins a
  protein. Detected by :func:`app.domain.analysis.check_reading_frames` over
  the merged state; see :func:`merge_logs`.

The second is the interesting one: two edits can be perfectly non-overlapping
and still combine into a premature stop codon.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.analysis import check_reading_frames
from app.domain.circular import segments, span_length
from app.domain.models import (
    ConstructState,
    Feature,
    Operation,
    OperationError,
)
from app.domain.replay import apply_operation, replay


class MergeError(ValueError):
    """The two logs do not share the ancestor they claim to."""


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------

def spans_overlap(
    a_start: int, a_end: int, b_start: int, b_end: int,
    length: int, is_circular: bool,
) -> bool:
    """Do two (possibly origin-crossing) half-open ranges share a base?"""
    for s1, e1 in segments(a_start, a_end, length, is_circular):
        for s2, e2 in segments(b_start, b_end, length, is_circular):
            if s1 < e2 and s2 < e1:
                return True
    return False


def strictly_inside(
    pos: int, start: int, end: int, length: int, is_circular: bool
) -> bool:
    """Is the junction at ``pos`` strictly between the ends of ``[start, end)``?

    On a circular construct a wrapping range contains the junction at 0, since
    the bases either side of it are both covered.
    """
    if is_circular and start >= end:
        return pos > start or pos < end
    return start < pos < end


# ---------------------------------------------------------------------------
# coordinate transforms
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Rotate:
    """``set_origin``: relabels every coordinate, modifies no base."""

    origin: int
    length: int

    def point(self, q: int) -> int:
        return (q - self.origin) % self.length

    def span(self, s: int, e: int) -> tuple[int, int]:
        return (
            (s - self.origin) % self.length,
            ((e - 1 - self.origin) % self.length) + 1,
        )

    def hits_point(self, q: int, is_circular: bool) -> bool:
        return False

    def hits_span(self, s: int, e: int, is_circular: bool) -> bool:
        return False


@dataclass(frozen=True)
class _Insert:
    """New bases at a point. The same endpoint rule as feature rebasing."""

    pos: int
    added: int
    length: int

    def point(self, q: int) -> int:
        return q + self.added if q >= self.pos else q

    def span(self, s: int, e: int) -> tuple[int, int]:
        return (
            s + self.added if s >= self.pos else s,
            e + self.added if e > self.pos else e,
        )

    def hits_point(self, q: int, is_circular: bool) -> bool:
        # Two insertions at the same spot are an ordering choice, not a clash.
        return False

    def hits_span(self, s: int, e: int, is_circular: bool) -> bool:
        # The target branch put bases inside a region this operation consumes.
        return strictly_inside(self.pos, s, e, self.length, is_circular)


@dataclass(frozen=True)
class _Delete:
    """Bases removed from ``[start, end)``, always a linear range here."""

    start: int
    end: int
    length: int

    @property
    def removed(self) -> int:
        return self.end - self.start

    def point(self, q: int) -> int:
        return q if q <= self.start else q - self.removed

    def span(self, s: int, e: int) -> tuple[int, int]:
        return (
            s if s <= self.start else s - self.removed,
            e if e <= self.start else e - self.removed,
        )

    def hits_point(self, q: int, is_circular: bool) -> bool:
        return self.start < q < self.end

    def hits_span(self, s: int, e: int, is_circular: bool) -> bool:
        return spans_overlap(
            s, e, self.start, self.end, self.length, is_circular
        )


@dataclass(frozen=True)
class _Revcomp:
    """Reversal in place: outside the range every coordinate is unchanged."""

    start: int
    end: int
    length: int

    def point(self, q: int) -> int:
        return q

    def span(self, s: int, e: int) -> tuple[int, int]:
        return (s, e)

    def hits_point(self, q: int, is_circular: bool) -> bool:
        return self.start < q < self.end

    def hits_span(self, s: int, e: int, is_circular: bool) -> bool:
        return spans_overlap(
            s, e, self.start, self.end, self.length, is_circular
        )


_Transform = _Rotate | _Insert | _Delete | _Revcomp


def _linear_steps(
    cutting: bool, start: int, end: int, length: int, is_circular: bool
) -> list[_Transform]:
    """Express a possibly-wrapping range edit as linear transforms.

    Exactly the decomposition :mod:`app.domain.replay` applies: rotate the range
    to the front, do the linear thing, and for length-preserving edits rotate
    back so coordinates outside the range are untouched.
    """
    factory = _Delete if cutting else _Revcomp
    if not (is_circular and start >= end):
        return [factory(start, end, length)]
    span = span_length(start, end, length, True)
    if cutting:
        return [_Rotate(start, length), _Delete(0, span, length)]
    return [
        _Rotate(start, length),
        _Revcomp(0, span, length),
        _Rotate((length - start) % length, length),
    ]


def transforms_for(op: Operation, before: ConstructState) -> list[_Transform]:
    """Decompose one operation into the coordinate transforms it performs.

    Operations whose range crosses the origin are expressed the same way
    :mod:`app.domain.replay` applies them: rotate the range to the front, do
    the linear thing, rotate back where the length is preserved.
    """
    n = len(before.sequence)
    circular = before.is_circular
    p = op.payload

    def normalized(start: int, end: int) -> tuple[int, int, bool]:
        return start, end, circular and start >= end

    if op.kind == "insert":
        pos = p["pos"]
        if pos > n and circular and n:
            pos %= n
        return [_Insert(pos, len(p["seq"]), n)]

    if op.kind in {"delete", "replace"}:
        start, end, wraps = normalized(p["start"], p["end"])
        out = _linear_steps(True, start, end, n, circular)
        if op.kind == "replace":
            cut = out[-1]
            assert isinstance(cut, _Delete)
            out.append(_Insert(0 if wraps else cut.start, len(p["seq"]), n - cut.removed))
        return out

    if op.kind == "revcomp_region":
        start, end, _ = normalized(p["start"], p["end"])
        return _linear_steps(False, start, end, n, circular)

    if op.kind == "set_origin":
        return [_Rotate(p["pos"] % n if n else 0, n)]

    # The feature operations and the suppression operations move no bases.
    return []


# ---------------------------------------------------------------------------
# rebasing one operation
# ---------------------------------------------------------------------------

@dataclass
class Conflict:
    """One branch operation that cannot be replayed onto the target."""

    branch_index: int
    kind: str
    reason: str
    detail: str


class _Blocked(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def _map_point(pos: int, steps: list[_Transform], is_circular: bool) -> int:
    for step in steps:
        if step.hits_point(pos, is_circular):
            raise _Blocked(
                "position_removed",
                f"position {pos + 1} was removed or rewritten on the target "
                "branch",
            )
        pos = step.point(pos)
    return pos


def _map_span(
    start: int, end: int, steps: list[_Transform], is_circular: bool
) -> tuple[int, int]:
    for step in steps:
        if step.hits_span(start, end, is_circular):
            raise _Blocked(
                "overlapping_edit",
                f"range {start + 1}..{end} was also edited on the target "
                "branch",
            )
        start, end = step.span(start, end)
    return start, end


def carry_window(
    window: dict | None, steps: list[_Transform], is_circular: bool
) -> dict | None:
    """Move a suppression's evidence window onto the target's coordinates.

    Never a conflict, and that asymmetry is deliberate. A suppression moves no
    bases: the worst an overlapping edit on the target can do is invalidate the
    evidence it was recorded against, and the digest already catches that. So
    an edit inside the window drops the coordinates instead of refusing the
    merge, and the finding resurfaces marked stale on the other side. A note
    somebody left about a warning should never be able to block a merge.
    """
    if not window or window.get("start") is None or window.get("end") is None:
        return window
    moved = dict(window)
    try:
        moved["start"], moved["end"] = _map_span(
            window["start"], window["end"], steps, is_circular
        )
    except _Blocked:
        moved["start"] = moved["end"] = None
    return moved


def rebase_operation(
    op: Operation,
    steps: list[_Transform],
    target: ConstructState,
    *,
    construct_id: str,
    index: int,
) -> Operation | None:
    """Re-express ``op`` against the target's tip.

    Returns ``None`` when the operation is already satisfied there (both
    branches removed the same feature, say). Raises :class:`_Blocked` when the
    two histories genuinely disagree.
    """
    payload = dict(op.payload)
    circular = target.is_circular

    if op.kind in {"insert", "set_origin"}:
        payload["pos"] = _map_point(payload["pos"], steps, circular)
    elif op.kind in {"delete", "replace", "revcomp_region"}:
        payload["start"], payload["end"] = _map_span(
            payload["start"], payload["end"], steps, circular
        )
    elif op.kind == "add_feature":
        feature = dict(payload["feature"])
        if any(f.id == feature.get("id") for f in target.features):
            raise _Blocked(
                "duplicate_feature",
                f"a feature with id {feature.get('id')!r} already exists on "
                "the target branch",
            )
        feature["start"], feature["end"] = _map_span(
            feature["start"], feature["end"], steps, circular
        )
        payload["feature"] = feature
    elif op.kind == "remove_feature":
        if not any(f.id == payload["feature_id"] for f in target.features):
            return None  # already gone on the target: convergent, not a clash
    elif op.kind == "update_feature":
        if not any(f.id == payload["feature_id"] for f in target.features):
            raise _Blocked(
                "feature_removed",
                f"feature {payload['feature_id']!r} was removed on the target "
                "branch",
            )
        patch = dict(payload["patch"])
        if "start" in patch and "end" in patch:
            patch["start"], patch["end"] = _map_span(
                patch["start"], patch["end"], steps, circular
            )
        elif "start" in patch or "end" in patch:
            raise _Blocked(
                "partial_coordinate_patch",
                "a patch that moves only one end of a feature cannot be "
                "rebased; send both start and end",
            )
        payload["patch"] = patch
    elif op.kind == "suppress_finding":
        if not any(f.id == payload["feature_id"] for f in target.features):
            # The feature is gone on the target, so is the finding it raised.
            return None
        payload["window"] = carry_window(payload.get("window"), steps, circular)
    elif op.kind == "unsuppress_finding":
        if not any(
            (s.rule_id, s.feature_id) == (payload["rule_id"], payload["feature_id"])
            for s in target.suppressions
        ):
            return None  # nothing to lift: convergent, not a clash

    return Operation(
        id=op.id,
        construct_id=construct_id,
        index=index,
        kind=op.kind,
        payload=payload,
        reverted=False,
    )


def carry_step(
    step: _Transform,
    through: list[_Transform],
    length: int,
    is_circular: bool,
) -> list[_Transform]:
    """Re-express one of the target's transforms after a branch operation.

    Needed because branch operation *i* is written against the branch state
    after operations 0..i-1, not against the ancestor. The chain that maps the
    branch's frame onto the merged frame therefore has to be carried across
    each branch operation as it is applied.
    """
    if isinstance(step, _Rotate):
        return [_Rotate(_map_point(step.origin, through, is_circular), length)]
    if isinstance(step, _Insert):
        return [
            _Insert(
                _map_point(step.pos, through, is_circular), step.added, length
            )
        ]
    start, end = _map_span(step.start, step.end, through, is_circular)
    # A rotation on the branch side can push a linear edit across the origin.
    return _linear_steps(isinstance(step, _Delete), start, end, length, is_circular)


# ---------------------------------------------------------------------------
# merging two logs
# ---------------------------------------------------------------------------

@dataclass
class MergeResult:
    """Everything the API needs to decide whether to commit a merge."""

    #: Branch operations re-expressed against the target's tip.
    rebased: list[Operation] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    #: Branch operations the target had already satisfied.
    skipped: list[Conflict] = field(default_factory=list)
    #: Frame problems the merge *introduces*, absent from both tips.
    new_frame_issues: list = field(default_factory=list)
    merged_state: ConstructState | None = None

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)

    @property
    def breaks_biology(self) -> bool:
        return any(i.blocking for i in self.new_frame_issues)

    @property
    def clean(self) -> bool:
        return not self.has_conflicts and not self.breaks_biology


def _issue_keys(state: ConstructState) -> set[tuple[str, str]]:
    return {(i.feature_name, i.problem) for i in check_reading_frames(state)}


def merge_logs(
    base_sequence: str,
    base_features: list[Feature],
    ancestor_ops: list[Operation],
    target_ops: list[Operation],
    branch_ops: list[Operation],
    *,
    is_circular: bool = True,
    construct_id: str = "",
) -> MergeResult:
    """Rebase ``branch_ops`` onto the target and report what breaks.

    ``ancestor_ops`` is the history both sides share; ``target_ops`` and
    ``branch_ops`` are what each added after the fork. All three are expected
    to be live (non-reverted).
    """
    ancestor = replay(
        base_sequence, base_features, ancestor_ops, is_circular=is_circular
    )
    result = MergeResult()

    # Walk the target's new operations, collecting the coordinate transform
    # each one performs against the state it actually saw.
    steps: list[_Transform] = []
    state = ancestor.model_copy(deep=True)
    for op in target_ops:
        steps.extend(transforms_for(op, state))
        try:
            apply_operation(state, op)
        except OperationError as exc:  # pragma: no cover - stored ops are valid
            raise MergeError(
                f"the target's own history no longer replays: {exc}"
            ) from exc
    target_state = state

    ancestor_issues = _issue_keys(ancestor)
    target_issues = _issue_keys(target_state)
    branch_state = replay(
        base_sequence,
        base_features,
        ancestor_ops + branch_ops,
        is_circular=is_circular,
    )
    branch_issues = _issue_keys(branch_state)

    # Rebase each branch operation, apply it, then carry the transform chain
    # across it so the next operation is rebased from the frame it was
    # actually written against. Stops at the first conflict: a merge is
    # all-or-nothing, and later reports derived from a broken frame would be
    # noise rather than information.
    merged = target_state.model_copy(deep=True)
    walking = ancestor.model_copy(deep=True)
    next_index = len(ancestor_ops) + len(target_ops)

    for offset, op in enumerate(branch_ops):
        try:
            rebased = rebase_operation(
                op, steps, merged,
                construct_id=construct_id or op.construct_id,
                index=next_index,
            )
        except _Blocked as blocked:
            result.conflicts.append(
                Conflict(offset, op.kind, blocked.reason, blocked.detail)
            )
            return result

        if rebased is None:
            result.skipped.append(
                Conflict(
                    offset, op.kind, "already_applied",
                    "the target branch had already made this change",
                )
            )
        else:
            try:
                apply_operation(merged, rebased)
            except OperationError as exc:
                result.conflicts.append(
                    Conflict(
                        offset, op.kind, "invalid_after_rebase",
                        f"the rebased operation does not apply: {exc}",
                    )
                )
                return result
            result.rebased.append(rebased)
            next_index += 1

        branch_steps = transforms_for(op, walking)
        apply_operation(walking, op)
        if branch_steps:
            carried: list[_Transform] = []
            for step in steps:
                carried.extend(
                    carry_step(
                        step, branch_steps, len(walking.sequence),
                        walking.is_circular,
                    )
                )
            steps = carried

    result.merged_state = merged
    # Only blame the merge for damage neither side had on its own.
    already_known = ancestor_issues | target_issues | branch_issues
    result.new_frame_issues = [
        issue
        for issue in check_reading_frames(merged)
        if (issue.feature_name, issue.problem) not in already_known
    ]
    return result
