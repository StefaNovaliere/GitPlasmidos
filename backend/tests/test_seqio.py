"""Import / export against real pUC19 records and hand-built edge cases."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.models import ConstructState
from app.domain.replay import replay
from app.domain.seqio import (
    ImportError_,
    detect_format,
    export_fasta,
    export_genbank,
    parse_sequence_file,
)
from tests.conftest import feat, ops

DATA = Path(__file__).parent / "data"
PUC19 = DATA / "puc19_annotated.gb"
PUC19_NCBI = DATA / "puc19_M77789.gb"


def signature(features):
    """Everything about a feature except its (generated) id."""
    return [
        (f.name, f.kind, f.start, f.end, f.strand, f.color, f.truncated)
        for f in features
    ]


@pytest.fixture
def puc19():
    return parse_sequence_file(PUC19.read_text(), PUC19.name)


# --------------------------------------------------------------------------
# acceptance criterion 1: import pUC19
# --------------------------------------------------------------------------

def test_puc19_imports_with_the_expected_length_and_topology(puc19):
    assert len(puc19.sequence) == 2686
    assert puc19.is_circular is True
    assert puc19.name == "pUC19"


def test_puc19_features_are_imported_with_their_names_and_strands(puc19):
    by_name = {f.name: f for f in puc19.features}
    assert "lacZalpha" in by_name
    assert "bla" in by_name
    # lacZalpha is annotated on the complement strand at 146..469 (1-based)
    lacz = next(f for f in puc19.features if f.name == "lacZalpha" and f.kind == "CDS")
    assert (lacz.start, lacz.end, lacz.strand) == (145, 469, -1)
    bla = next(f for f in puc19.features if f.name == "bla" and f.kind == "CDS")
    assert (bla.start, bla.end, bla.strand) == (1625, 2486, -1)


def test_puc19_malformed_location_is_skipped_with_a_warning(puc19):
    # The fixture keeps one real-world misaligned location line.
    assert len(puc19.features) == 18
    assert any("unparseable location" in w for w in puc19.warnings)


def test_ncbi_puc19_record_imports():
    rec = parse_sequence_file(PUC19_NCBI.read_text(), PUC19_NCBI.name)
    assert len(rec.sequence) == 2686
    assert rec.is_circular is True
    assert len(rec.features) == 8


def test_qualifier_priority_prefers_label_then_gene_then_product():
    gb = _minimal_genbank(
        [
            ("CDS", "1..9", ['/gene="g"', '/product="p"']),
            ("CDS", "10..18", ['/product="only_product"']),
            ("CDS", "19..27", ['/label="lbl"', '/gene="g2"']),
        ]
    )
    names = [f.name for f in parse_sequence_file(gb, "t.gb").features]
    assert names == ["g", "only_product", "lbl"]


def test_feature_without_any_name_qualifier_falls_back_to_its_type():
    gb = _minimal_genbank([("terminator", "1..9", [])])
    assert [f.name for f in parse_sequence_file(gb, "t.gb").features] == ["terminator"]


# --------------------------------------------------------------------------
# format detection, validation, multi-record
# --------------------------------------------------------------------------

def test_detect_format_from_extension_and_from_content():
    assert detect_format(">x\nACGT\n") == "fasta"
    assert detect_format("LOCUS x\n") == "genbank"
    assert detect_format("ACGT", "thing.fasta") == "fasta"
    with pytest.raises(ImportError_):
        detect_format("not a sequence file")


def test_fasta_import_has_no_features_and_warns_about_topology():
    rec = parse_sequence_file(">demo plasmid\nACGTACGTAC\nGTAC\n", "demo.fasta")
    assert rec.sequence == "ACGTACGTACGTAC"
    assert rec.features == []
    assert rec.is_circular is None
    assert any("topology" in w for w in rec.warnings)


def test_only_the_first_record_is_imported_and_the_rest_reported():
    rec = parse_sequence_file(">a\nACGT\n>b\nTTTT\n", "two.fasta")
    assert rec.sequence == "ACGT"
    assert any("only the first" in w for w in rec.warnings)


def test_non_iupac_characters_are_rejected():
    with pytest.raises(ImportError_):
        parse_sequence_file(">a\nACGTXZ\n", "bad.fasta")


def test_iupac_ambiguity_codes_are_accepted():
    rec = parse_sequence_file(">a\nACGTNRYSWKMBDHV\n", "amb.fasta")
    assert len(rec.sequence) == 15


def test_empty_file_is_rejected():
    with pytest.raises(ImportError_):
        parse_sequence_file("   \n", "empty.fasta")


def test_lowercase_input_is_uppercased():
    assert parse_sequence_file(">a\nacgt\n", "a.fasta").sequence == "ACGT"


# --------------------------------------------------------------------------
# acceptance criterion 4: GenBank round trip
# --------------------------------------------------------------------------

def test_genbank_round_trip_is_state_preserving(puc19):
    state = ConstructState(
        sequence=puc19.sequence, features=puc19.features, is_circular=True
    )
    back = parse_sequence_file(export_genbank(state, "pUC19"), "rt.gb")
    assert back.sequence == state.sequence
    assert back.is_circular is True
    assert signature(back.features) == signature(state.features)


def test_round_trip_survives_an_edit_and_exports_current_not_base_state(puc19):
    # Delete 500 bp out of the middle, then round-trip the *derived* state.
    state = replay(
        puc19.sequence,
        puc19.features,
        ops(("delete", {"start": 700, "end": 1200})),
    )
    assert state.length == 2186
    back = parse_sequence_file(export_genbank(state, "pUC19_edited"), "rt.gb")
    assert back.sequence == state.sequence
    assert len(back.sequence) == 2186
    assert signature(back.features) == signature(state.features)
    # the truncation flag rode along
    assert any(f.truncated for f in back.features)


def test_round_trip_preserves_an_origin_crossing_feature():
    seq = "".join("ACGT"[i % 4] for i in range(100))
    state = ConstructState(
        sequence=seq,
        features=[feat(95, 7, id="a", name="cross", kind="CDS", strand=1)],
        is_circular=True,
    )
    gb = export_genbank(state, "wrap")
    assert "join(96..100,1..7)" in gb
    back = parse_sequence_file(gb, "rt.gb")
    assert signature(back.features) == signature(state.features)


def test_round_trip_preserves_an_origin_crossing_feature_on_the_minus_strand():
    seq = "".join("ACGT"[i % 4] for i in range(100))
    state = ConstructState(
        sequence=seq,
        features=[feat(95, 7, id="a", name="cross", kind="CDS", strand=-1)],
        is_circular=True,
    )
    back = parse_sequence_file(export_genbank(state, "wrap"), "rt.gb")
    assert signature(back.features) == signature(state.features)


def test_round_trip_preserves_colours():
    state = ConstructState(
        sequence="ACGT" * 25,
        features=[feat(0, 10, id="a", name="x")],
        is_circular=True,
    )
    state.features[0].color = "#ff8800"
    back = parse_sequence_file(export_genbank(state, "c"), "rt.gb")
    assert back.features[0].color == "#ff8800"


def test_exported_genbank_declares_the_right_topology():
    circular = ConstructState(sequence="ACGT" * 25, is_circular=True)
    linear = ConstructState(sequence="ACGT" * 25, is_circular=False)
    assert "circular" in export_genbank(circular, "c").splitlines()[0]
    assert "linear" in export_genbank(linear, "l").splitlines()[0]


def test_exported_genbank_locus_name_is_sanitised():
    state = ConstructState(sequence="ACGT" * 25)
    first = export_genbank(state, "a name with spaces and a very long tail").splitlines()[0]
    assert first.startswith("LOCUS       a_name_with_spac")


# --------------------------------------------------------------------------
# FASTA export
# --------------------------------------------------------------------------

def test_fasta_export_round_trips_the_sequence(puc19):
    state = ConstructState(sequence=puc19.sequence, features=puc19.features)
    back = parse_sequence_file(export_fasta(state, "pUC19"), "rt.fasta")
    assert back.sequence == puc19.sequence
    assert back.features == []


def test_fasta_export_header_mentions_length_and_topology(puc19):
    header = export_fasta(
        ConstructState(sequence=puc19.sequence), "pUC19"
    ).splitlines()[0]
    assert "2686 bp" in header and "circular" in header


def _minimal_genbank(features: list[tuple[str, str, list[str]]]) -> str:
    """Assemble a tiny GenBank file, respecting the fixed column layout."""
    lines = [
        "LOCUS       TEST                      40 bp    DNA     circular SYN "
        "01-JAN-2020",
        "DEFINITION  test.",
        "FEATURES             Location/Qualifiers",
    ]
    for ftype, location, qualifiers in features:
        lines.append(f"     {ftype:<16}{location}")
        lines.extend(" " * 21 + q for q in qualifiers)
    lines += ["ORIGIN", f"        1 {'acgt' * 10}", "//", ""]
    return "\n".join(lines)
