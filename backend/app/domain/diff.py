"""Comparing two derived states.

Two constructs forked from a common ancestor diverge in three ways worth
seeing separately: which bases differ, which annotations moved, and which
operations each side ran. This module covers the first two; the third is just
the two operation logs, which the API reads straight from the database.

Circular molecules complicate the first: a ``set_origin`` on one side rewrites
every coordinate without changing the molecule at all, and a naive diff would
report the whole plasmid as rearranged. The comparison therefore normalises the
origin first and reports the shift separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

from app.domain.circular import rotate_sequence, slice_span
from app.domain.models import ConstructState, Feature
from app.domain.replay import rebase_rotate

#: How many bases of a changed block to carry in the payload, per side.
MAX_SEGMENT_BASES = 400

#: Shortest common block trusted to anchor two molecules' origins together.
MIN_ORIGIN_ANCHOR = 12

#: How many candidate rotations to score before giving up. Each costs one
#: extra alignment, so this stays small.
MAX_ORIGIN_CANDIDATES = 3



#: Equal runs shorter than this are absorbed into the change around them.
#: difflib aligns characters, not biology: replacing 50 bases with 10 G's
#: leaves it free to match a stray G either side and report three changes
#: where a reader wants one.
MIN_EQUAL_RUN = 10

#: Feature fields whose change is worth reporting.
COMPARED_FIELDS = ("name", "kind", "start", "end", "strand", "color", "truncated")


@dataclass
class SequenceSegment:
    """One block of the alignment: ``equal``, ``insert``, ``delete``, ``replace``."""

    op: str
    left_start: int
    left_end: int
    right_start: int
    right_end: int
    #: Only carried for changed blocks, and capped at ``MAX_SEGMENT_BASES``.
    left_seq: str = ""
    right_seq: str = ""
    truncated: bool = False


@dataclass
class SequenceDiff:
    identical: bool
    identity: float
    bases_added: int
    bases_removed: int
    #: How far the right molecule was rotated to line its origin up with the
    #: left's. Non-zero means somebody ran ``set_origin`` on one side.
    origin_shift: int
    segments: list[SequenceSegment] = field(default_factory=list)

    @property
    def changed_segments(self) -> list[SequenceSegment]:
        return [s for s in self.segments if s.op != "equal"]


@dataclass
class FeatureChange:
    before: Feature
    after: Feature
    changed_fields: list[str]


@dataclass
class FeatureDiff:
    added: list[Feature] = field(default_factory=list)
    removed: list[Feature] = field(default_factory=list)
    #: Genuinely different: renamed, restranded, or covering different bases.
    changed: list[FeatureChange] = field(default_factory=list)
    #: Merely displaced by an indel elsewhere - same bases, new coordinates.
    #: Kept apart because a 30 bp deletion near the origin moves every feature
    #: on the plasmid, and listing all of them as "changed" buries the one
    #: that actually was.
    shifted: list[FeatureChange] = field(default_factory=list)
    unchanged: int = 0


@dataclass
class ConstructDiff:
    sequence: SequenceDiff
    features: FeatureDiff


# ---------------------------------------------------------------------------
# origin normalisation
# ---------------------------------------------------------------------------

def candidate_origin_shifts(
    left: str, right: str, is_circular: bool
) -> list[int]:
    """Rotations of ``right`` worth scoring, most-supported first.

    Every substantial block the two molecules share sits at some offset; if
    they are the same molecule cut at different points, the true rotation is
    one of those offsets. Blocks vote by length.

    Anchoring on a fixed probe from the start instead looks cheaper but fails
    on repetitive sequence: a probe of "ACGTACGT..." matches at position 0 of
    a molecule rotated by any multiple of the repeat period, which is exactly
    when the answer matters. Taking only the single longest block fails too —
    an edit near the middle splits the molecule, and the larger half can vote
    for a rotation that is off by the length of the edit. So several
    candidates are proposed here and :func:`diff_sequences` keeps whichever
    actually explains the two sequences best.
    """
    if not is_circular or not left or not right or left == right:
        return []
    length = len(right)
    votes: dict[int, int] = {}
    for a, b, size in SequenceMatcher(
        None, left, right, autojunk=False
    ).get_matching_blocks():
        if size >= MIN_ORIGIN_ANCHOR:
            shift = (b - a) % length
            votes[shift] = votes.get(shift, 0) + size
    ranked = sorted(votes, key=lambda shift: -votes[shift])
    return [shift for shift in ranked if shift][:MAX_ORIGIN_CANDIDATES]


def find_origin_shift(left: str, right: str, is_circular: bool) -> int:
    """The best-supported rotation, or 0 when there is none."""
    candidates = candidate_origin_shifts(left, right, is_circular)
    return candidates[0] if candidates else 0


# ---------------------------------------------------------------------------
# sequence
# ---------------------------------------------------------------------------

def _clip(seq: str) -> tuple[str, bool]:
    if len(seq) <= MAX_SEGMENT_BASES:
        return seq, False
    return seq[:MAX_SEGMENT_BASES], True


def _segments(left: str, right: str) -> tuple[list[SequenceSegment], int]:
    # autojunk MUST stay off. Its heuristic drops elements appearing in more
    # than 1% of positions, and in a four-letter alphabet that is every base:
    # with it on, a 2 kb pair differing by one edit matches at 0.41 instead
    # of 0.99.
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    segments: list[SequenceSegment] = []
    matched = 0
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        segment = SequenceSegment(op, i1, i2, j1, j2)
        if op == "equal":
            matched += i2 - i1
        else:
            left_seq, left_cut = _clip(left[i1:i2])
            right_seq, right_cut = _clip(right[j1:j2])
            segment.left_seq = left_seq
            segment.right_seq = right_seq
            segment.truncated = left_cut or right_cut
        segments.append(segment)
    return segments, matched


def _coalesce(
    segments: list[SequenceSegment], left: str, right: str
) -> list[SequenceSegment]:
    """Merge changes separated by only a few matching bases into one block."""
    out: list[SequenceSegment] = []
    pending: list[SequenceSegment] = []

    def flush() -> None:
        trailing: list[SequenceSegment] = []
        while pending and pending[-1].op == "equal":
            trailing.insert(0, pending.pop())
        if pending:
            first, last = pending[0], pending[-1]
            merged = SequenceSegment(
                "insert"
                if first.left_start == last.left_end
                else "delete"
                if first.right_start == last.right_end
                else "replace",
                first.left_start,
                last.left_end,
                first.right_start,
                last.right_end,
            )
            left_seq, left_cut = _clip(left[merged.left_start : merged.left_end])
            right_seq, right_cut = _clip(right[merged.right_start : merged.right_end])
            merged.left_seq = left_seq
            merged.right_seq = right_seq
            merged.truncated = left_cut or right_cut
            out.append(merged)
        pending.clear()
        out.extend(trailing)

    for segment in segments:
        if segment.op != "equal" or segment.left_end - segment.left_start < MIN_EQUAL_RUN and pending:
            pending.append(segment)
        else:
            flush()
            out.append(segment)
    flush()
    return out


def _looks_rotated(diff: SequenceDiff, left: str, right: str) -> bool:
    """Is this alignment consistent with the two molecules being cut differently?

    Material moved across the origin has to show up as a change touching an
    end, so an edit confined to the middle — the common case, a branch and its
    parent sharing an origin — costs one alignment and no rotation search.
    Requiring *both* ends would be tighter but is unsafe: a repeat that spans
    the origin can align spuriously at one end and hide the rotation. The
    candidates are scored anyway, so a false positive costs time, not accuracy.

    Identity is no use as the signal here: a rotation by a single base reads as
    a 0.9996-identity edit.
    """
    changed = diff.changed_segments
    if not changed:
        return False
    first, last = changed[0], changed[-1]
    return (
        first.left_start == 0
        or first.right_start == 0
        or last.left_end == len(left)
        or last.right_end == len(right)
    )


def diff_sequences(left: str, right: str, is_circular: bool) -> SequenceDiff:
    """Align two sequences, normalising the origin on circular molecules."""
    def scored(used_shift: int, candidate: str) -> SequenceDiff:
        segments, matched = _segments(left, candidate)
        span = max(len(left), len(candidate)) or 1
        # Counted before coalescing: absorbing a matched base into a change
        # block must not make it count as both added and removed.
        diff = SequenceDiff(
            identical=left == candidate,
            identity=matched / span,
            bases_added=sum(
                s.right_end - s.right_start
                for s in segments
                if s.op in {"insert", "replace"}
            ),
            bases_removed=sum(
                s.left_end - s.left_start
                for s in segments
                if s.op in {"delete", "replace"}
            ),
            origin_shift=used_shift,
            segments=_coalesce(segments, left, candidate),
        )
        return diff

    best = scored(0, right)
    if not is_circular or not _looks_rotated(best, left, right):
        return best
    for shift in candidate_origin_shifts(left, right, is_circular):
        rival = scored(shift, rotate_sequence(right, shift))
        if rival.identity > best.identity:
            best = rival
    return best


# ---------------------------------------------------------------------------
# features
# ---------------------------------------------------------------------------

def _changed_fields(before: Feature, after: Feature) -> list[str]:
    return [
        name
        for name in COMPARED_FIELDS
        if getattr(before, name) != getattr(after, name)
    ]


def _covers_the_same_bases(
    before: Feature,
    after: Feature,
    left_seq: str | None,
    right_seq: str | None,
    is_circular: bool,
) -> bool:
    if left_seq is None or right_seq is None:
        return False
    return slice_span(
        left_seq, before.start, before.end, is_circular
    ) == slice_span(right_seq, after.start, after.end, is_circular)


def diff_features(
    left: list[Feature],
    right: list[Feature],
    *,
    left_seq: str | None = None,
    right_seq: str | None = None,
    is_circular: bool = True,
) -> FeatureDiff:
    """Pair features up by id, then by identity, and report what moved.

    Ids survive editing, so they are the reliable pairing for two branches of
    the same construct. Unrelated constructs share no ids, so anything left
    over is matched on name, kind and strand before being called added or
    removed.
    """
    diff = FeatureDiff()
    left_by_id = {f.id: f for f in left}
    right_by_id = {f.id: f for f in right}

    unpaired_left, unpaired_right = [], []
    for feature in left:
        if feature.id not in right_by_id:
            unpaired_left.append(feature)
    for feature in right:
        if feature.id not in left_by_id:
            unpaired_right.append(feature)

    pairs = [
        (left_by_id[fid], right_by_id[fid])
        for fid in left_by_id
        if fid in right_by_id
    ]

    # Second pass for constructs that never shared ids.
    remaining = list(unpaired_right)
    still_missing = []
    for feature in unpaired_left:
        key = (feature.name, feature.kind, feature.strand)
        match = next(
            (
                other
                for other in remaining
                if (other.name, other.kind, other.strand) == key
            ),
            None,
        )
        if match is None:
            still_missing.append(feature)
        else:
            remaining.remove(match)
            pairs.append((feature, match))

    diff.removed = still_missing
    diff.added = remaining
    for before, after in pairs:
        fields = _changed_fields(before, after)
        if not fields:
            diff.unchanged += 1
            continue
        change = FeatureChange(before, after, fields)
        only_moved = set(fields) <= {"start", "end"}
        if only_moved and _covers_the_same_bases(
            before, after, left_seq, right_seq, is_circular
        ):
            diff.shifted.append(change)
        else:
            diff.changed.append(change)
    diff.changed.sort(key=lambda c: c.after.start)
    diff.shifted.sort(key=lambda c: c.after.start)
    diff.added.sort(key=lambda f: f.start)
    diff.removed.sort(key=lambda f: f.start)
    return diff


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def diff_states(left: ConstructState, right: ConstructState) -> ConstructDiff:
    """Compare two derived states.

    When the right molecule's origin has moved, its features are rotated to
    match before being compared, so a bare ``set_origin`` shows up as an origin
    shift rather than as every annotation having moved.
    """
    is_circular = left.is_circular and right.is_circular
    sequence = diff_sequences(left.sequence, right.sequence, is_circular)

    right_features = right.features
    if sequence.origin_shift:
        right_features = rebase_rotate(
            right_features, sequence.origin_shift, len(right.sequence)
        )
    right_sequence = right.sequence
    if sequence.origin_shift:
        right_sequence = rotate_sequence(right_sequence, sequence.origin_shift)
    return ConstructDiff(
        sequence,
        diff_features(
            left.features,
            right_features,
            left_seq=left.sequence,
            right_seq=right_sequence,
            is_circular=is_circular,
        ),
    )
