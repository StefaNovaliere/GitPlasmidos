"""Sequence analysis: restriction sites, ORFs and composition.

Everything here is read-only with respect to the construct: it consumes a
:class:`~app.domain.models.ConstructState` and reports on it.
"""

from __future__ import annotations

from dataclasses import dataclass

from Bio.Restriction import CommOnly, RestrictionBatch
from Bio.Seq import Seq

from app.domain.circular import revcomp, segments, slice_span
from app.domain.models import ConstructState, Feature

#: The cloning workhorses. Biopython ships ``AllEnzymes`` (1088) and
#: ``CommOnly`` (623 commercially available); neither is a usable checkbox list,
#: so the default is this curated set of enzymes people actually clone with.
#: ``?all=true`` widens the search to the whole ``CommOnly`` batch.
COMMON_ENZYMES = (
    "AatII", "AccI", "AflII", "AgeI", "ApaI", "ApaLI", "AscI", "AsiSI",
    "AvrII", "BamHI", "BbsI", "BglII", "BsaI", "BsiWI", "BsmBI", "BspEI",
    "BsrGI", "BssHII", "BstBI", "ClaI", "DraI", "EagI", "EcoRI", "EcoRV",
    "FseI", "HindIII", "HpaI", "KpnI", "MfeI", "MluI", "NcoI", "NdeI",
    "NheI", "NotI", "NruI", "PacI", "PmeI", "PmlI", "PspOMI", "PstI",
    "PvuI", "PvuII", "SacI", "SacII", "SalI", "SbfI", "ScaI", "SmaI",
    "SnaBI", "SpeI", "SphI", "SspI", "StuI", "SwaI", "XbaI", "XhoI",
    "XmaI",
)

#: NCBI translation table 1 (the standard code).
STANDARD_TABLE = 1
START_CODON = "ATG"
STOP_CODONS = frozenset({"TAA", "TAG", "TGA"})

#: Feature kinds whose reading frame is meaningful.
CODING_KINDS = frozenset({"CDS"})

#: Table 1 only starts at ATG, but bacterial plasmids routinely use GTG/TTG,
#: so those are accepted rather than reported as a missing start.
START_CODONS = frozenset({"ATG", "GTG", "TTG"})


@dataclass
class EnzymeSite:
    name: str
    site: str
    #: 0-based positions on the top strand where the enzyme cuts.
    cut_positions: list[int]
    #: "5'", "3'" or "blunt".
    overhang: str

    @property
    def cuts(self) -> int:
        return len(self.cut_positions)


@dataclass
class Orf:
    start: int
    end: int
    strand: int
    length: int
    frame: int
    protein: str


# ---------------------------------------------------------------------------
# restriction enzymes
# ---------------------------------------------------------------------------

def _build_batch(names: list[str] | None, use_all: bool) -> RestrictionBatch:
    if names:
        wanted = set(names)
        return RestrictionBatch([e for e in CommOnly if str(e) in wanted])
    if use_all:
        return CommOnly
    known = set(COMMON_ENZYMES)
    return RestrictionBatch([e for e in CommOnly if str(e) in known])


def _overhang(enzyme) -> str:
    if enzyme.is_blunt():
        return "blunt"
    return "5'" if enzyme.is_5overhang() else "3'"


def find_restriction_sites(
    sequence: str,
    is_circular: bool,
    *,
    single_cutters_only: bool = True,
    use_all: bool = False,
    names: list[str] | None = None,
) -> list[EnzymeSite]:
    """Digest ``sequence`` in silico.

    Biopython's ``RestrictionBatch.search`` is wraparound-aware when told the
    molecule is circular, so origin-spanning sites are found for free. Cut
    positions come back 1-based and are converted to 0-based here.
    """
    if not sequence:
        return []
    batch = _build_batch(names, use_all)
    found = batch.search(Seq(sequence), linear=not is_circular)

    out: list[EnzymeSite] = []
    for enzyme, positions in found.items():
        if not positions:
            continue
        if single_cutters_only and len(positions) != 1:
            continue
        out.append(
            EnzymeSite(
                name=str(enzyme),
                site=str(enzyme.site),
                cut_positions=sorted((p - 1) % len(sequence) for p in positions),
                overhang=_overhang(enzyme),
            )
        )
    return sorted(out, key=lambda e: (e.cut_positions[0], e.name))


# ---------------------------------------------------------------------------
# open reading frames
# ---------------------------------------------------------------------------

def _scan_frame(hay: str, frame: int, limit: int, min_length: int):
    """Yield ``(start, end)`` half-open ORFs in ``hay`` for one reading frame."""
    open_at: int | None = None
    for i in range(frame, len(hay) - 2, 3):
        codon = hay[i : i + 3]
        if open_at is None:
            if codon == START_CODON:
                open_at = i
            continue
        if codon in STOP_CODONS:
            end = i + 3
            if end - open_at >= min_length and end - open_at <= limit:
                yield open_at, end
            open_at = None


def _translate(seq: str) -> str:
    return str(Seq(seq).translate(table=STANDARD_TABLE, to_stop=True))


def find_orfs(
    sequence: str, is_circular: bool, *, min_length: int = 300
) -> list[Orf]:
    """Find ORFs in all six frames, optionally running through the origin.

    An ORF runs from an ``ATG`` to the first in-frame stop codon and its length
    *includes* that stop codon. On a circular construct the search runs over
    ``sequence + sequence`` so that ORFs crossing the origin are found; those
    are reported with ``start > end``. Hits are de-duplicated and no ORF may be
    longer than the molecule itself.
    """
    n = len(sequence)
    if n < 3:
        return []
    hay = sequence + sequence if is_circular else sequence
    # No ORF may wrap the molecule more than once.
    limit = n
    seen: set[tuple[int, int, int]] = set()
    orfs: list[Orf] = []

    def record(h_start: int, h_end: int, strand: int, nt: str) -> None:
        if h_start >= n:
            return
        start = h_start % n
        end = ((h_end - 1) % n) + 1
        key = (start, end, strand)
        if key in seen:
            return
        seen.add(key)
        orfs.append(
            Orf(
                start=start,
                end=end,
                strand=strand,
                length=h_end - h_start,
                frame=(h_start % 3) + 1 if strand == 1 else -((h_start % 3) + 1),
                protein=_translate(nt),
            )
        )

    for frame in range(3):
        for s, e in _scan_frame(hay, frame, limit, min_length):
            record(s, e, 1, hay[s:e])

    rc = revcomp(hay)
    total = len(hay)
    for frame in range(3):
        for s, e in _scan_frame(rc, frame, limit, min_length):
            # Map back to plus-strand coordinates.
            record(total - e, total - s, -1, rc[s:e])

    return sorted(orfs, key=lambda o: (o.start, -o.length, o.strand))


# ---------------------------------------------------------------------------
# composition
# ---------------------------------------------------------------------------

def gc_content(sequence: str) -> float:
    if not sequence:
        return 0.0
    return (sequence.count("G") + sequence.count("C")) / len(sequence)


# ---------------------------------------------------------------------------
# reading-frame integrity
# ---------------------------------------------------------------------------

#: A frame problem serious enough to mean the protein is gone. Step 3 (merging
#: two operation logs) will refuse a merge that introduces one of these.
BLOCKING_PROBLEMS = frozenset({"frameshift", "premature_stop"})


@dataclass
class FrameIssue:
    """One thing wrong with a coding feature's reading frame."""

    feature_id: str
    feature_name: str
    #: frameshift | premature_stop | no_stop_codon | no_start_codon | too_short
    problem: str
    severity: str  # "error" | "warning" | "info"
    detail: str
    #: 1-based codon number, for ``premature_stop``.
    codon: int | None = None
    #: Genomic half-open span of the offending codon, so a viewer can point at
    #: it. ``None`` when the span crosses the origin and cannot be written as
    #: one interval.
    stop_start: int | None = None
    stop_end: int | None = None
    #: Genomic half-open span that still makes protein. Everything else in the
    #: feature is downstream of the stop and is never translated.
    translated_start: int | None = None
    translated_end: int | None = None

    @property
    def blocking(self) -> bool:
        return self.problem in BLOCKING_PROBLEMS


def coding_sequence(state: ConstructState, feature: Feature) -> str:
    """The feature's bases, 5'->3' along its own strand.

    The span is assembled *before* anything else happens to it, so a feature
    that crosses the origin yields one continuous coding sequence and the codon
    straddling position 0 stays intact. Reading the two segments separately is
    the classic way to get this wrong.
    """
    span = slice_span(state.sequence, feature.start, feature.end, state.is_circular)
    return revcomp(span) if feature.strand == -1 else span


def cds_positions(state: ConstructState, feature: Feature) -> list[int]:
    """Genomic indices of a coding feature's bases, in reading order.

    For a minus-strand feature that is the reverse of genomic order, which is
    what makes it possible to map a codon number back onto the map.
    """
    positions = [
        p
        for start, end in segments(
            feature.start, feature.end, len(state.sequence), state.is_circular
        )
        for p in range(start, end)
    ]
    return positions[::-1] if feature.strand == -1 else positions


def _contiguous_span(positions: list[int]) -> tuple[int | None, int | None]:
    """``(start, end)`` if these genomic indices form one run, else ``None``s."""
    if not positions:
        return None, None
    low, high = min(positions), max(positions)
    if high - low + 1 != len(positions):
        return None, None  # crosses the origin; no single interval says it
    return low, high + 1


def check_reading_frames(
    state: ConstructState, *, kinds: frozenset[str] = CODING_KINDS
) -> list[FrameIssue]:
    """Report coding features whose reading frame no longer makes a protein.

    Cheap to run and deliberately separate from :func:`app.domain.replay.replay`:
    replay owns coordinates, this owns meaning. Both the read endpoints and (in
    time) the merge gate call it over the same derived state.
    """
    issues: list[FrameIssue] = []
    for f in state.features:
        if f.kind not in kinds:
            continue
        seq = coding_sequence(state, f)

        if len(seq) < 3:
            issues.append(
                FrameIssue(
                    f.id, f.name, "too_short", "error",
                    f"{len(seq)} bp cannot hold a codon",
                )
            )
            continue

        if len(seq) % 3:
            extra = len(seq) % 3
            issues.append(
                FrameIssue(
                    f.id, f.name, "frameshift", "error",
                    f"{len(seq)} bp is not a multiple of 3 "
                    f"({extra} base{'s' if extra > 1 else ''} out of frame)",
                )
            )
            # Translating a frameshifted CDS only produces downstream noise.
            continue

        protein = str(Seq(seq).translate(table=STANDARD_TABLE))
        stop_at = protein.find("*")
        if stop_at != -1 and stop_at < len(protein) - 1:
            reading = cds_positions(state, f)
            stop_start, stop_end = _contiguous_span(
                reading[stop_at * 3 : stop_at * 3 + 3]
            )
            translated_start, translated_end = _contiguous_span(
                reading[: stop_at * 3]
            )
            issues.append(
                FrameIssue(
                    f.id, f.name, "premature_stop", "error",
                    f"stop codon at codon {stop_at + 1} of {len(protein)}, "
                    f"truncating the protein to {stop_at} aa",
                    codon=stop_at + 1,
                    stop_start=stop_start,
                    stop_end=stop_end,
                    translated_start=translated_start,
                    translated_end=translated_end,
                )
            )
        elif not protein.endswith("*"):
            issues.append(
                FrameIssue(
                    f.id, f.name, "no_stop_codon", "warning",
                    "the last codon is not a stop codon",
                )
            )

        if seq[:3] not in START_CODONS:
            issues.append(
                FrameIssue(
                    f.id, f.name, "no_start_codon", "info",
                    f"starts with {seq[:3]}, not ATG/GTG/TTG",
                )
            )
    return issues
