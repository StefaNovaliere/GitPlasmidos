"""FASTA / GenBank import and export on top of ``Bio.SeqIO``.

Import is deliberately forgiving (a real GenBank file in the wild has malformed
lines, joins, and no single convention for feature names) while export aims at
a file SnapGene and ApE can open.
"""

from __future__ import annotations

import io
import re
import warnings
from dataclasses import dataclass, field

from Bio import BiopythonParserWarning, SeqIO
from Bio.Seq import Seq
from Bio.SeqFeature import (
    AfterPosition,
    BeforePosition,
    CompoundLocation,
    ExactPosition,
    SeqFeature,
    SimpleLocation,
)
from Bio.SeqRecord import SeqRecord

from app.domain.models import ConstructState, Feature, SequenceError, validate_sequence

#: Qualifiers consulted, in order, to name an imported feature.
NAME_QUALIFIERS = ("label", "gene", "product", "standard_name", "note")

#: ApE / SnapGene colour qualifiers, understood on both import and export.
FWD_COLOR_QUALIFIER = "ApEinfo_fwdcolor"
REV_COLOR_QUALIFIER = "ApEinfo_revcolor"

_FASTA_EXT = {".fa", ".fas", ".fasta", ".fna", ".ffn", ".seq", ".txt"}
_GENBANK_EXT = {".gb", ".gbk", ".genbank", ".gbff", ".ape"}


class ImportError_(ValueError):
    """Raised when a submitted file cannot be turned into a construct."""


@dataclass
class ImportedRecord:
    """What :func:`parse_sequence_file` hands back to the API layer."""

    name: str
    sequence: str
    features: list[Feature]
    #: ``None`` when the format carries no topology information (FASTA).
    is_circular: bool | None = None
    description: str = ""
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def detect_format(data: str, filename: str | None = None) -> str:
    """Return ``"fasta"`` or ``"genbank"``, preferring the file extension."""
    if filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext in _GENBANK_EXT:
            return "genbank"
        if ext in _FASTA_EXT:
            return "fasta"
    head = data.lstrip()
    if head.startswith(">"):
        return "fasta"
    if head.startswith("LOCUS"):
        return "genbank"
    raise ImportError_(
        "Unrecognised file format: expected FASTA (starting with '>') or "
        "GenBank (starting with 'LOCUS')."
    )


def _feature_name(qualifiers: dict, fallback: str) -> str:
    for key in NAME_QUALIFIERS:
        values = qualifiers.get(key)
        if values:
            name = str(values[0]).strip().replace("\n", " ")
            if name:
                # Long /note values make terrible labels; keep them short.
                return name if len(name) <= 60 else name[:57] + "..."
    return fallback


def _feature_color(qualifiers: dict) -> str | None:
    for key in (FWD_COLOR_QUALIFIER, REV_COLOR_QUALIFIER):
        values = qualifiers.get(key)
        if values:
            candidate = str(values[0]).strip()
            if re.fullmatch(r"#[0-9A-Fa-f]{6}", candidate):
                return candidate.lower()
    return None


def _span_from_location(loc, seq_len: int) -> tuple[tuple[int, int], str | None]:
    """Collapse a Biopython location into a single ``(start, end)`` span.

    Returns the span plus an optional warning. A two-part join that touches both
    ends of the molecule is an origin-crossing feature and is rejoined into
    ``start > end``; any other join is flattened to its outer bounds.
    """
    parts = sorted(loc.parts, key=lambda p: int(p.start))
    if len(parts) == 1:
        return (int(parts[0].start), int(parts[0].end)), None
    if (
        len(parts) == 2
        and int(parts[0].start) == 0
        and int(parts[1].end) == seq_len
    ):
        return (int(parts[1].start), int(parts[0].end)), None
    return (
        (int(parts[0].start), int(parts[-1].end)),
        "join with {} parts flattened to its outer bounds".format(len(parts)),
    )


def _is_partial(loc) -> bool:
    return any(
        isinstance(p.start, BeforePosition) or isinstance(p.end, AfterPosition)
        for p in loc.parts
    )


def _features_from_record(record: SeqRecord) -> tuple[list[Feature], list[str]]:
    out: list[Feature] = []
    warns: list[str] = []
    seq_len = len(record.seq)
    for i, bf in enumerate(record.features):
        if bf.location is None:
            warns.append(
                f"feature #{i + 1} ({bf.type}) has an unparseable location and "
                "was skipped"
            )
            continue
        (start, end), note = _span_from_location(bf.location, seq_len)
        if note:
            warns.append(f"feature #{i + 1} ({bf.type}): {note}")
        if not (0 <= start < seq_len) or not (0 <= end <= seq_len):
            warns.append(
                f"feature #{i + 1} ({bf.type}) lies outside the sequence and "
                "was skipped"
            )
            continue
        if start == end:
            warns.append(
                f"feature #{i + 1} ({bf.type}) is zero-length and was skipped"
            )
            continue
        truncated = _is_partial(bf.location) or any(
            v == "true" for v in bf.qualifiers.get("truncated", [])
        )
        out.append(
            Feature(
                id=f"f{i:04d}",
                name=_feature_name(bf.qualifiers, bf.type),
                kind=bf.type or "misc_feature",
                start=start,
                end=end,
                strand=-1 if bf.location.strand == -1 else 1,
                color=_feature_color(bf.qualifiers),
                truncated=truncated,
            )
        )
    return out, warns


def parse_sequence_file(
    data: str | bytes, filename: str | None = None
) -> ImportedRecord:
    """Parse the *first* record of a FASTA or GenBank file."""
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError:
            data = data.decode("latin-1")
    if not data.strip():
        raise ImportError_("The uploaded file is empty.")

    fmt = detect_format(data, filename)
    warns: list[str] = []

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", BiopythonParserWarning)
        try:
            records = list(SeqIO.parse(io.StringIO(data), fmt))
        except Exception as exc:
            raise ImportError_(f"Could not parse the file as {fmt}: {exc}") from exc
    for w in caught:
        warns.append(f"parser: {w.message}")

    if not records:
        raise ImportError_(f"No {fmt} record found in the file.")
    if len(records) > 1:
        warns.append(
            f"The file holds {len(records)} records; only the first "
            f"({records[0].id}) was imported."
        )

    record = records[0]
    try:
        sequence = validate_sequence(str(record.seq))
    except SequenceError as exc:
        raise ImportError_(str(exc)) from exc
    if not sequence:
        raise ImportError_("The first record has no sequence.")

    features, feature_warns = _features_from_record(record)
    warns.extend(feature_warns)

    is_circular: bool | None = None
    if fmt == "genbank":
        topology = str(record.annotations.get("topology", "")).lower()
        if topology in {"circular", "linear"}:
            is_circular = topology == "circular"
    if is_circular is None:
        warns.append(
            "The file does not declare a topology; the construct was assumed "
            "to be circular."
        )

    name = (record.name or record.id or "imported").strip()
    if name in {"", "unknown", "<unknown name>", "<unknown id>"}:
        name = "imported"
    return ImportedRecord(
        name=name,
        sequence=sequence,
        features=features,
        is_circular=is_circular,
        description=(record.description or "").strip(),
        warnings=warns,
    )


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def _locus_name(name: str) -> str:
    """GenBank LOCUS names are a single token; keep it to 16 safe characters."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_")
    return (cleaned or "construct")[:16]


def _to_biopython_feature(f: Feature, length: int, is_circular: bool) -> SeqFeature:
    from app.domain.circular import segments

    strand = -1 if f.strand == -1 else 1
    qualifiers: dict[str, list[str]] = {"label": [f.name]}
    if f.color:
        qualifiers[FWD_COLOR_QUALIFIER] = [f.color]
        qualifiers[REV_COLOR_QUALIFIER] = [f.color]
    if f.truncated:
        qualifiers["truncated"] = ["true"]

    segs = segments(f.start, f.end, length, is_circular)
    if len(segs) == 1:
        s, e = segs[0]
        start_pos = BeforePosition(s) if f.truncated else ExactPosition(s)
        end_pos = AfterPosition(e) if f.truncated else ExactPosition(e)
        location = SimpleLocation(start_pos, end_pos, strand=strand)
    else:
        # Origin-crossing: emit the standard join(start..length,1..end).
        (s0, e0), (s1, e1) = segs
        first = SimpleLocation(
            BeforePosition(s0) if f.truncated else ExactPosition(s0),
            ExactPosition(e0),
            strand=strand,
        )
        second = SimpleLocation(
            ExactPosition(s1),
            AfterPosition(e1) if f.truncated else ExactPosition(e1),
            strand=strand,
        )
        parts = [first, second] if strand == 1 else [second, first]
        location = CompoundLocation(parts, operator="join")
    return SeqFeature(location=location, type=f.kind, qualifiers=qualifiers)


def state_to_record(state: ConstructState, name: str) -> SeqRecord:
    """Build a Biopython record from the *current* derived state."""
    record = SeqRecord(
        Seq(state.sequence),
        id=_locus_name(name),
        name=_locus_name(name),
        description=name,
    )
    record.annotations["molecule_type"] = "DNA"
    record.annotations["topology"] = "circular" if state.is_circular else "linear"
    record.annotations["data_file_division"] = "SYN"
    record.features = [
        _to_biopython_feature(f, state.length, state.is_circular)
        for f in state.features
    ]
    return record


def export_genbank(state: ConstructState, name: str) -> str:
    handle = io.StringIO()
    SeqIO.write(state_to_record(state, name), handle, "genbank")
    return handle.getvalue()


def export_fasta(state: ConstructState, name: str) -> str:
    record = SeqRecord(
        Seq(state.sequence),
        id=_locus_name(name),
        description=f"{name} | {state.length} bp | "
        f"{'circular' if state.is_circular else 'linear'}",
    )
    handle = io.StringIO()
    SeqIO.write(record, handle, "fasta")
    return handle.getvalue()
