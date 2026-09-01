"""Wraparound helpers for circular molecules.

A plasmid has no beginning, so every coordinate computation here has to cope
with intervals that cross the origin (``start > end``). The strategy used
throughout the domain layer is:

* decompose a possibly-wrapping interval into one or two **linear** segments,
* apply the plain linear rule to each segment,
* recombine the segments back into a single (possibly wrapping) interval.

Operations whose *range* wraps are handled differently: the construct is
rotated so that the range starts at 0, the linear operation is applied, and the
rotation is undone. See :mod:`app.domain.replay`.
"""

from __future__ import annotations

_COMPLEMENT = str.maketrans(
    "ACGTNRYSWKMBDHVacgtnryswkmbdhv",
    "TGCANYRSWMKVHDBtgcanyrswmkvhdb",
)

Segment = tuple[int, int]


def revcomp(seq: str) -> str:
    """Reverse complement, IUPAC-aware."""
    return seq.translate(_COMPLEMENT)[::-1]


def segments(start: int, end: int, length: int, is_circular: bool) -> list[Segment]:
    """Split ``[start, end)`` into non-empty linear segments.

    On a circular construct ``start >= end`` means the interval crosses the
    origin; ``start == end`` is the whole molecule (zero-length intervals are
    rejected before they reach here).
    """
    if not is_circular or start < end:
        return [(start, end)] if end > start else []
    out = [(start, length), (0, end)]
    return [(s, e) for s, e in out if e > s]


def recombine(segs: list[Segment], length: int, is_circular: bool) -> Segment | None:
    """Inverse of :func:`segments`.

    Two segments that touch the origin (one ending at ``length``, the next
    starting at 0) are merged back into a single wrapping interval. Returns
    ``None`` when nothing survived.
    """
    segs = [(s, e) for s, e in segs if e > s]
    if not segs:
        return None
    if (
        is_circular
        and len(segs) == 2
        and segs[0][1] == length
        and segs[1][0] == 0
    ):
        return (segs[0][0], segs[1][1])
    if len(segs) == 1:
        return segs[0]
    # Disjoint pieces: keep the longest one. Reachable only if a caller feeds
    # segments that no longer touch the origin.
    return max(segs, key=lambda p: p[1] - p[0])


def span_length(start: int, end: int, length: int, is_circular: bool) -> int:
    """Number of bases covered by ``[start, end)``."""
    return sum(e - s for s, e in segments(start, end, length, is_circular))


def covered_positions(
    start: int, end: int, length: int, is_circular: bool
) -> set[int]:
    """Explicit position set. Only for tests and small sequences."""
    out: set[int] = set()
    for s, e in segments(start, end, length, is_circular):
        out.update(range(s, e))
    return out


def slice_span(seq: str, start: int, end: int, is_circular: bool) -> str:
    """Extract ``[start, end)``, following the wraparound when needed."""
    return "".join(seq[s:e] for s, e in segments(start, end, len(seq), is_circular))


def rotate_sequence(seq: str, origin: int) -> str:
    """Return ``seq`` rotated so that position ``origin`` becomes position 0."""
    if not seq:
        return seq
    origin %= len(seq)
    return seq[origin:] + seq[:origin]


def rotate_coord(pos: int, origin: int, length: int) -> int:
    """Map a coordinate through a :func:`rotate_sequence` of the same origin."""
    if length == 0:
        return 0
    return (pos - origin) % length


def find_pattern(
    seq: str, pattern: str, is_circular: bool, *, both_strands: bool = False
) -> list[tuple[int, int]]:
    """Locate ``pattern`` in ``seq``, searching across the origin when circular.

    Returns ``(start, strand)`` pairs with ``start`` in ``[0, len(seq))``.
    Circular search works on ``seq + seq[:k-1]`` so that hits spanning the
    origin are found exactly once (a hit starting at ``i >= len(seq)`` would be
    a duplicate of the one at ``i - len(seq)`` and is dropped).
    """
    k = len(pattern)
    n = len(seq)
    if k == 0 or n == 0:
        return []
    hits: list[tuple[int, int]] = []
    strands: list[tuple[str, int]] = [(pattern.upper(), 1)]
    if both_strands:
        rc = revcomp(pattern.upper())
        if rc != pattern.upper():
            strands.append((rc, -1))
    haystack = seq + seq[: k - 1] if (is_circular and k > 1 and k - 1 <= n) else seq
    for pat, strand in strands:
        i = haystack.find(pat)
        while i != -1:
            if i < n:
                hits.append((i, strand))
            i = haystack.find(pat, i + 1)
    return sorted(set(hits))
