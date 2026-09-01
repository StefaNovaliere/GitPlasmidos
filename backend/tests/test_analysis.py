"""Restriction mapping, ORF finding and composition, checked against pUC19."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.analysis import find_orfs, find_restriction_sites, gc_content
from app.domain.circular import revcomp, rotate_sequence
from app.domain.seqio import parse_sequence_file

DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="module")
def puc19_seq():
    gb = (DATA / "puc19_annotated.gb").read_text()
    return parse_sequence_file(gb, "puc19.gb").sequence


# --------------------------------------------------------------------------
# restriction enzymes
# --------------------------------------------------------------------------

def test_puc19_polylinker_single_cutters_are_in_the_documented_order(puc19_seq):
    sites = find_restriction_sites(puc19_seq, True)
    order = [s.name for s in sites if 390 <= s.cut_positions[0] <= 450]
    # pUC19's MCS, 5'->3' on the published map.
    assert order == [
        "EcoRI", "SacI", "KpnI", "XmaI", "SmaI", "BamHI", "XbaI",
        "SalI", "AccI", "PstI", "SbfI", "SphI", "HindIII",
    ]


def test_scai_cuts_puc19_once_inside_bla(puc19_seq):
    sites = {s.name: s for s in find_restriction_sites(puc19_seq, True)}
    assert sites["ScaI"].cut_positions == [2179]
    assert sites["ScaI"].overhang == "blunt"


def test_ecori_overhang_is_reported_as_five_prime(puc19_seq):
    sites = {s.name: s for s in find_restriction_sites(puc19_seq, True)}
    assert sites["EcoRI"].site == "GAATTC"
    assert sites["EcoRI"].overhang == "5'"


def test_single_cutter_filter_is_what_narrows_the_list(puc19_seq):
    single = find_restriction_sites(puc19_seq, True)
    every = find_restriction_sites(puc19_seq, True, single_cutters_only=False)
    assert len(every) > len(single)
    assert all(s.cuts == 1 for s in single)
    assert any(s.cuts > 1 for s in every)


def test_all_widens_the_batch_beyond_the_curated_list(puc19_seq):
    curated = find_restriction_sites(puc19_seq, True)
    widened = find_restriction_sites(puc19_seq, True, use_all=True)
    assert len(widened) > len(curated)
    assert {s.name for s in curated} <= {s.name for s in widened}


def test_names_filter_restricts_the_search(puc19_seq):
    sites = find_restriction_sites(puc19_seq, True, names=["EcoRI", "HindIII"])
    assert {s.name for s in sites} == {"EcoRI", "HindIII"}


def test_a_site_spanning_the_origin_is_found_only_when_circular(puc19_seq):
    # Rotate so the EcoRI site (396..401) straddles the origin.
    rotated = rotate_sequence(puc19_seq, 399)
    circular = {s.name for s in find_restriction_sites(rotated, True)}
    linear = {s.name for s in find_restriction_sites(rotated, False)}
    assert "EcoRI" in circular
    assert "EcoRI" not in linear


def test_no_enzymes_on_an_empty_sequence():
    assert find_restriction_sites("", True) == []


# --------------------------------------------------------------------------
# ORFs
# --------------------------------------------------------------------------

def test_puc19_orfs_land_exactly_on_lacz_alpha_and_bla(puc19_seq):
    orfs = {(o.start, o.end, o.strand) for o in find_orfs(puc19_seq, True)}
    # Same coordinates the GenBank file annotates for lacZalpha and bla.
    assert (145, 469, -1) in orfs
    assert (1625, 2486, -1) in orfs


def test_orf_length_includes_the_stop_codon_and_protein_does_not(puc19_seq):
    bla = next(
        o for o in find_orfs(puc19_seq, True) if (o.start, o.end) == (1625, 2486)
    )
    assert bla.length == 861
    assert len(bla.protein) == 861 // 3 - 1
    assert bla.protein.startswith("MSIQHFRVALIPFFAAFCLPVFA")


def test_min_length_filters_orfs(puc19_seq):
    assert len(find_orfs(puc19_seq, True, min_length=900)) < len(
        find_orfs(puc19_seq, True, min_length=300)
    )
    assert all(o.length >= 600 for o in find_orfs(puc19_seq, True, min_length=600))


def test_an_orf_crossing_the_origin_is_reported_once_with_start_after_end(puc19_seq):
    # Rotate so that bla (1625..2486, minus strand) straddles the origin.
    rotated = rotate_sequence(puc19_seq, 2000)
    orfs = find_orfs(rotated, True)
    wrapped = [o for o in orfs if o.start > o.end]
    assert len(wrapped) == 1
    (bla,) = wrapped
    assert (bla.start, bla.end, bla.strand, bla.length) == (2311, 486, -1, 861)


def test_the_same_orf_is_not_reported_twice_across_the_origin(puc19_seq):
    orfs = find_orfs(rotate_sequence(puc19_seq, 2000), True)
    keys = [(o.start, o.end, o.strand) for o in orfs]
    assert len(keys) == len(set(keys))


def test_a_linear_construct_does_not_find_wraparound_orfs(puc19_seq):
    rotated = rotate_sequence(puc19_seq, 2000)
    assert all(o.start < o.end for o in find_orfs(rotated, False))


def test_orfs_are_found_on_both_strands():
    #  ATG + 100 sense codons + TAA  ->  306 bp
    orf = "ATG" + "AAG" * 100 + "TAA"
    plus = find_orfs("CC" + orf + "CC", False, min_length=300)
    assert [(o.start, o.end, o.strand, o.length) for o in plus] == [(2, 308, 1, 306)]
    assert plus[0].protein == "M" + "K" * 100
    minus = find_orfs("CC" + revcomp(orf) + "CC", False, min_length=300)
    assert [(o.start, o.end, o.strand, o.length) for o in minus] == [(2, 308, -1, 306)]
    assert minus[0].protein == "M" + "K" * 100


def test_an_orf_without_a_stop_codon_is_not_reported():
    assert find_orfs("ATG" + "AAG" * 200, False, min_length=30) == []


def test_short_sequences_are_handled():
    assert find_orfs("AT", True) == []


# --------------------------------------------------------------------------
# composition
# --------------------------------------------------------------------------

def test_gc_content_of_puc19(puc19_seq):
    assert gc_content(puc19_seq) == pytest.approx(0.5063, abs=1e-4)


def test_gc_content_edge_cases():
    assert gc_content("") == 0.0
    assert gc_content("GCGC") == 1.0
    assert gc_content("ATAT") == 0.0
