"""Running rules over a derived state.

Everything here is pure, and it leans on the wraparound helpers that already
exist: a rule's search window is just another interval that may cross the
origin, and a motif search is the same one enzyme sites use.

The one thing worth reading closely is direction. A rule's region is expressed
in the *target's reading direction*, so "upstream" of a minus-strand gene means
higher coordinates, not lower ones. Getting that backwards silently reports
every rule against the wrong half of the molecule.
"""

from __future__ import annotations

import re

from app.domain.circular import revcomp, segments, slice_span
from app.domain.models import ConstructState, Feature
from app.domain.rules.models import Finding, Look, Region, Rule, Target

#: IUPAC code -> the bases it stands for.
_IUPAC_BASES = {
    "A": "A", "C": "C", "G": "G", "T": "T",
    "R": "AG", "Y": "CT", "S": "GC", "W": "AT", "K": "GT", "M": "AC",
    "B": "CGT", "D": "AGT", "H": "ACT", "V": "ACG", "N": "ACGT",
}


def motif_pattern(motif: str) -> re.Pattern[str]:
    """Compile an IUPAC motif into a regex.

    Plain string search is not enough: consensus sequences are written
    degenerately (``TTGACR``), and matching those literally finds nothing.
    """
    return re.compile(
        "".join(f"[{_IUPAC_BASES[base]}]" for base in motif.upper())
    )


def find_motif(
    sequence: str, motif: str, is_circular: bool
) -> list[tuple[int, int]]:
    """Every place ``motif`` occurs, following the wraparound when circular."""
    pattern = motif_pattern(motif)
    length = len(sequence)
    width = len(motif)
    if width == 0 or length == 0:
        return []
    haystack = (
        sequence + sequence[: width - 1]
        if is_circular and width > 1 and width - 1 <= length
        else sequence
    )
    hits = []
    for match in pattern.finditer(haystack):
        start = match.start()
        if start < length:  # a hit past the end is the wraparound counted twice
            hits.append((start, start + width))
    # Overlapping occurrences: finditer skips them, so sweep again by hand.
    for start in range(len(haystack) - width + 1):
        if start < length and pattern.fullmatch(haystack[start : start + width]):
            hits.append((start, start + width))
    return sorted(set(hits))


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

def region_span(
    region: Region, feature: Feature, length: int, is_circular: bool
) -> tuple[int, int]:
    """The window a rule searches, in genomic coordinates.

    Read in the feature's own direction: for a minus-strand feature its 5' end
    is at ``feature.end``, so "upstream" runs to higher coordinates.
    """
    window = min(region.window, length)

    def wrap(value: int) -> int:
        return value % length if is_circular else max(0, min(length, value))

    if region.where == "inside":
        return feature.start, feature.end
    forward = feature.strand != -1
    if (region.where == "upstream") == forward:
        # Upstream of a plus-strand feature, or downstream of a minus one:
        # the bases just before `start`.
        return wrap(feature.start - window), feature.start
    return feature.end, wrap(feature.end + window)


def gap_to_feature(
    hit: tuple[int, int], feature: Feature, length: int, is_circular: bool
) -> int:
    """Bases between a hit and the feature's near edge, in reading direction."""
    hit_start, hit_end = hit
    forward = feature.strand != -1
    if forward:
        raw = feature.start - hit_end if hit_end <= feature.start else hit_start - feature.end
    else:
        raw = hit_start - feature.end if hit_start >= feature.end else feature.start - hit_end
    if raw < 0 and is_circular:
        raw += length
    return max(0, raw)


def _covered(start: int, end: int, length: int, is_circular: bool) -> set[int]:
    return {
        position
        for a, b in segments(start, end, length, is_circular)
        for position in range(a, b)
    }


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------

def _targets(target: Target, state: ConstructState) -> list[Feature]:
    wanted = {"any": (1, -1), "plus": (1,), "minus": (-1,)}[target.strand]
    return [
        f
        for f in state.features
        if f.kind == target.feature_kind and f.strand in wanted
    ]


def _strand_matches(look: Look, feature: Feature, hit_strand: int) -> bool:
    if look.strand == "any":
        return True
    same = hit_strand == feature.strand
    return same if look.strand == "same" else not same


def _hits(
    look: Look, window: tuple[int, int], feature: Feature, state: ConstructState
) -> list[tuple[int, int]]:
    length = len(state.sequence)
    start, end = window
    if look.feature_kind:
        inside = _covered(start, end, length, state.is_circular)
        return sorted(
            (other.start, other.end)
            for other in state.features
            if other.kind == look.feature_kind
            and other.id != feature.id
            and _strand_matches(look, feature, other.strand)
            and _covered(other.start, other.end, length, state.is_circular) & inside
        )

    window_seq = slice_span(state.sequence, start, end, state.is_circular)
    out: list[tuple[int, int]] = []
    strands = [(window_seq, 1)]
    if look.strand != "same" or feature.strand == -1:
        strands.append((revcomp(window_seq), -1))
    for text, orientation in strands:
        # A motif is searched on the strand the target reads, so a minus-strand
        # target looks at the reverse complement of its window.
        effective = orientation * (1 if feature.strand != -1 else -1)
        if not _strand_matches(look, feature, effective):
            continue
        for hit_start, hit_end in find_motif(text, look.motif or "", False):
            if orientation == 1:
                a, b = hit_start, hit_end
            else:
                a, b = len(text) - hit_end, len(text) - hit_start
            out.append(
                (
                    (start + a) % length if state.is_circular else start + a,
                    (start + b - 1) % length + 1 if state.is_circular else start + b,
                )
            )
    return sorted(set(out))


def _finding(
    rule: Rule, feature: Feature, message: str, span: tuple[int, int] | None
) -> Finding:
    return Finding(
        rule_id=rule.id,
        title=rule.title,
        severity=rule.severity,
        feature_id=feature.id,
        feature_name=feature.name,
        message=message,
        start=span[0] if span else None,
        end=span[1] if span else None,
        evidence=rule.evidence,
    )


def _render(rule: Rule, feature: Feature, distance: int | str = "?") -> str:
    return rule.message.format(
        feature=feature.name,
        motif=rule.look.motif or rule.look.feature_kind or "",
        window=rule.region.window,
        distance=distance,
    )


def evaluate(rule: Rule, state: ConstructState) -> list[Finding]:
    """Run one rule over a derived state."""
    length = len(state.sequence)
    if length == 0:
        return []
    findings: list[Finding] = []

    for feature in _targets(rule.target, state):
        window = region_span(rule.region, feature, length, state.is_circular)
        hits = _hits(rule.look, window, feature, state)

        if rule.expect.presence == "forbidden":
            findings.extend(
                _finding(rule, feature, _render(rule, feature), hit) for hit in hits
            )
            continue

        if not hits:
            findings.append(_finding(rule, feature, _render(rule, feature), None))
            continue

        if rule.expect.distance_min is None and rule.expect.distance_max is None:
            continue

        closest = min(
            hits, key=lambda h: gap_to_feature(h, feature, length, state.is_circular)
        )
        gap = gap_to_feature(closest, feature, length, state.is_circular)
        low, high = rule.expect.distance_min, rule.expect.distance_max
        if (low is not None and gap < low) or (high is not None and gap > high):
            findings.append(
                _finding(rule, feature, _render(rule, feature, gap), closest)
            )

    return findings


def lint(rules: list[Rule], state: ConstructState) -> list[Finding]:
    """Run a whole pack. Errors first, then by position."""
    order = {"error": 0, "warning": 1, "info": 2}
    findings = [f for rule in rules for f in evaluate(rule, state)]
    return sorted(findings, key=lambda f: (order[f.severity], f.start or 0))
