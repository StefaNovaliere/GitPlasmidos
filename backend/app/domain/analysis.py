"""Sequence analysis: restriction sites, ORFs and composition.

Everything here is read-only with respect to the construct: it consumes a
:class:`~app.domain.models.ConstructState` and reports on it.
"""

from __future__ import annotations

from dataclasses import dataclass

from Bio.Restriction import CommOnly, RestrictionBatch
from Bio.Seq import Seq

from app.domain.circular import revcomp

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


@dataclass
class EnzymeSite:
    name: str
    site: str
    #: 0-based positions on the top strand where the enzyme cuts.
    cut_positions: list[int]
    #: 0-based start of each recognition site occurrence.
    overhang: str  # "5'", "3'" or "blunt"

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
    limit = n if is_circular else n
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
