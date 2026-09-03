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
from app.domain.models import ConstructState, EvidenceWindow, Feature, Suppression
from app.domain.rules.models import (
    Finding,
    Look,
    Region,
    Rule,
    RuleSet,
    SuppressionState,
    Target,
    rule_digest,
    sha256_hex,
)

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
# evidence: what the rule actually read
# ---------------------------------------------------------------------------

#: How much of a window is quoted back to the reader.
EXCERPT_LIMIT = 48


def _excerpt(text: str) -> str:
    """A quotable piece of a window: the whole thing, or both ends of it."""
    if len(text) <= EXCERPT_LIMIT:
        return text
    half = (EXCERPT_LIMIT - 1) // 2
    return f"{text[:half]}…{text[-half:]}"


def window_text(
    span: tuple[int, int], feature: Feature, state: ConstructState
) -> str:
    """The window as the rule read it: in the target's reading direction.

    Reading direction, not genomic order, is what makes a suppression survive a
    ``revcomp_region`` that flips the whole cassette: the bases the rule sees
    are the same ones, so the digest is the same and the decision stands.
    """
    text = slice_span(state.sequence, span[0], span[1], state.is_circular)
    return text if feature.strand != -1 else revcomp(text)


def evidence_window(
    span: tuple[int, int], feature: Feature, state: ConstructState
) -> EvidenceWindow:
    """Hash the bases a rule looked at, and remember where they were."""
    text = window_text(span, feature, state)
    return EvidenceWindow(
        digest=sha256_hex(text),
        excerpt=_excerpt(text),
        start=span[0],
        end=span[1],
    )


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
    # Both physical strands of the window. Which of them a rule cares about is
    # decided below, per hit, by the strand it was found on: a hit in the
    # forward text is on the plus strand, one in the reverse complement is on
    # the minus strand. A ribosome binding site for a minus-strand gene reads
    # AGGAGG on the *gene's* strand, which is CCTCCT in the plus-strand text —
    # matching the literal AGGAGG there instead would accept exactly the
    # sequences that cannot work and report the ones that can.
    strands = [(window_seq, 1), (revcomp(window_seq), -1)]
    for text, orientation in strands:
        if not _strand_matches(look, feature, orientation):
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
    rule: Rule,
    feature: Feature,
    message: str,
    span: tuple[int, int] | None,
    window: EvidenceWindow,
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
        window=window,
        rule_digest=rule_digest(rule),
    )


def _render(
    rule: Rule, feature: Feature, template: str, distance: int | None = None
) -> str:
    return template.format(
        feature=feature.name,
        motif=rule.look.motif or rule.look.feature_kind or "",
        window=rule.region.window,
        distance="" if distance is None else distance,
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
        # Hashed once per target, not per finding: the evidence is the window
        # the rule inspected, which is the same for every finding it raises
        # about this feature.
        evidence = evidence_window(window, feature, state)

        if rule.expect.presence == "forbidden":
            findings.extend(
                _finding(rule, feature, _render(rule, feature, rule.message), hit, evidence)
                for hit in hits
            )
            continue

        if not hits:
            # Nothing found is its own diagnosis, and never carries a distance.
            findings.append(
                _finding(
                    rule,
                    feature,
                    _render(rule, feature, rule.message_missing or rule.message),
                    None,
                    evidence,
                )
            )
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
                _finding(
                    rule,
                    feature,
                    _render(rule, feature, rule.message, gap),
                    closest,
                    evidence,
                )
            )

    return findings


def apply_suppression(finding: Finding, suppression: Suppression | None) -> Finding:
    """Mark a finding against the decision somebody recorded about it.

    Three outcomes, and the middle one is the whole point:

    * no suppression — the finding stands,
    * a suppression whose evidence still matches — quiet, but still counted,
    * a suppression whose evidence moved on — the finding is back, carrying
      both readings, and the call goes to whoever made it.

    Invalidating a suppression automatically would have people re-suppressing
    every time they edit nearby, which is how a linter gets switched off.
    Carrying it silently would let it cover a problem introduced afterwards.
    Neither is acceptable, so a stale suppression is *visible*.
    """
    if suppression is None:
        return finding
    marked = finding.model_copy(deep=True)
    changed: str | None = None
    if suppression.rule_digest != finding.rule_digest:
        changed = "rule"
    elif finding.window is None or suppression.window.digest != finding.window.digest:
        changed = "evidence"
    marked.suppressed = changed is None
    marked.suppression = SuppressionState(
        reason=suppression.reason,
        stale=changed is not None,
        changed=changed,
        was=suppression.window.excerpt if changed else "",
        now=(finding.window.excerpt if finding.window else "") if changed else "",
        pack_digest=suppression.pack_digest,
    )
    return marked


def suppression_payload(finding: Finding, reason: str) -> dict:
    """The ``suppress_finding`` payload that silences ``finding``.

    Defined next to the engine that computed the evidence so that the shape of
    the payload has exactly one author. Callers pass the finding back rather
    than reconstructing which bases the rule read.
    """
    if finding.feature_id is None or finding.window is None:
        raise ValueError("a finding with no feature and no window cannot be suppressed")
    return {
        "rule_id": finding.rule_id,
        "feature_id": finding.feature_id,
        "reason": reason,
        "window": finding.window.model_dump(),
        "rule_digest": finding.rule_digest,
        "pack_digest": finding.pack_digest,
    }


def lint(pack: RuleSet, state: ConstructState) -> list[Finding]:
    """Run a whole pack over a state, honouring its suppressions.

    Takes the pack rather than a list of rules so that every finding can carry
    the digest of what produced it. A finding is only meaningful against a
    version of the rules, and that version has to travel with it.

    Suppressed findings are marked and kept, never dropped: a count of
    "3 findings, 1 suppressed" is auditable, and a hidden one rots.
    """
    order = {"error": 0, "warning": 1, "info": 2}
    silenced = {(s.rule_id, s.feature_id): s for s in state.suppressions}
    digest = pack.digest
    findings = []
    for rule in pack.rules:
        for finding in evaluate(rule, state):
            finding.pack_digest = digest
            findings.append(
                apply_suppression(finding, silenced.get((finding.rule_id, finding.feature_id)))
            )
    return sorted(
        findings,
        key=lambda f: (f.suppressed, order[f.severity], f.start or 0),
    )
