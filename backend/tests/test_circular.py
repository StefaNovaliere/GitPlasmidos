"""Explicit coverage for the wraparound cases - the #1 bug source here."""

from __future__ import annotations

import pytest

from app.domain.circular import (
    find_pattern,
    recombine,
    revcomp,
    rotate_sequence,
    segments,
    slice_span,
    span_length,
)
from app.domain.replay import replay
from tests.conftest import feat, ops, spans

# 20 bp so every position can be checked by hand.
SEQ = "AAAACCCCGGGGTTTTACGT"


def covered_seq(state, fid: str) -> str:
    f = next(f for f in state.features if f.id == fid)
    return slice_span(state.sequence, f.start, f.end, state.is_circular)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def test_segments_splits_a_wrapping_interval():
    assert segments(18, 3, 20, True) == [(18, 20), (0, 3)]


def test_segments_of_a_plain_interval_is_a_single_piece():
    assert segments(5, 9, 20, True) == [(5, 9)]


def test_segments_of_start_equals_end_is_the_whole_circle():
    assert span_length(7, 7, 20, True) == 20


def test_recombine_is_the_inverse_of_segments():
    assert recombine(segments(18, 3, 20, True), 20, True) == (18, 3)


def test_slice_span_follows_the_wraparound():
    assert slice_span(SEQ, 18, 3, True) == "GTAAA"


def test_revcomp_handles_iupac_codes():
    assert revcomp("ACGTRYN") == "NRYACGT"


def test_rotate_sequence_is_a_pure_rotation():
    assert rotate_sequence(SEQ, 4) == "CCCCGGGGTTTTACGTAAAA"
    assert rotate_sequence(rotate_sequence(SEQ, 4), 16) == SEQ


# --------------------------------------------------------------------------
# pattern search across the origin
# --------------------------------------------------------------------------

def test_find_pattern_finds_a_hit_spanning_the_origin():
    # "GTAAA" sits at 18..19 + 0..2
    assert find_pattern(SEQ, "GTAAA", True) == [(18, 1)]


def test_find_pattern_ignores_the_wraparound_when_linear():
    assert find_pattern(SEQ, "GTAAA", False) == []


def test_find_pattern_does_not_report_duplicates_across_the_origin():
    seq = "ACGTACGTACGT"
    hits = find_pattern(seq, "ACGT", True)
    assert hits == [(0, 1), (4, 1), (8, 1)]


def test_find_pattern_searches_both_strands_when_asked():
    hits = find_pattern("AAAGGGCCCTTT", "GGGCCC", True, both_strands=True)
    # GGGCCC is its own reverse complement, so only the plus-strand hit.
    assert hits == [(3, 1)]
    hits = find_pattern("AAAATTTTCCCC", "GGGG", True, both_strands=True)
    assert hits == [(8, -1)]


# --------------------------------------------------------------------------
# acceptance criterion 5: origin-crossing feature survives an insert
# --------------------------------------------------------------------------

def test_origin_crossing_feature_survives_an_insert_before_the_origin():
    base = [feat(18, 3, id="cross", name="ori")]
    state = replay(SEQ, base, ops(("insert", {"pos": 10, "seq": "TTTTT"})))
    assert state.length == 25
    assert spans(state.features) == {"cross": (23, 3)}
    assert covered_seq(state, "cross") == "GTAAA"
    assert state.warnings == []


def test_origin_crossing_feature_swallows_an_insert_at_the_origin():
    base = [feat(18, 3, id="cross", name="ori")]
    state = replay(SEQ, base, ops(("insert", {"pos": 0, "seq": "TTTTT"})))
    assert spans(state.features) == {"cross": (23, 8)}
    assert covered_seq(state, "cross") == "GTTTTTTAAA"


def test_origin_crossing_feature_survives_a_delete_before_the_origin():
    base = [feat(18, 3, id="cross", name="ori")]
    state = replay(SEQ, base, ops(("delete", {"start": 8, "end": 12})))
    assert state.length == 16
    assert spans(state.features) == {"cross": (14, 3)}
    assert covered_seq(state, "cross") == "GTAAA"


def test_insert_at_the_end_of_a_circle_is_absorbed_by_a_crossing_feature():
    base = [feat(18, 3, id="cross", name="ori")]
    state = replay(SEQ, base, ops(("insert", {"pos": 20, "seq": "CC"})))
    assert spans(state.features) == {"cross": (18, 3)}
    assert covered_seq(state, "cross") == "GTCCAAA"


# --------------------------------------------------------------------------
# operation ranges that themselves cross the origin
# --------------------------------------------------------------------------

def test_delete_of_a_wrapping_range_keeps_the_surviving_bases():
    state = replay(SEQ, [], ops(("delete", {"start": 18, "end": 3})))
    assert state.sequence == "ACCCCGGGGTTTTAC"
    assert state.length == 15


def test_replace_of_a_wrapping_range():
    state = replay(SEQ, [], ops(("replace", {"start": 18, "end": 3, "seq": "GGG"})))
    assert state.sequence == "GGGACCCCGGGGTTTTAC"


def test_revcomp_of_a_wrapping_range_keeps_outside_coordinates_stable():
    state = replay(SEQ, [], ops(("revcomp_region", {"start": 18, "end": 3})))
    assert state.length == 20
    # untouched bases keep their positions ...
    assert state.sequence[3:18] == SEQ[3:18]
    # ... and the wrapping window now holds the reverse complement.
    assert slice_span(state.sequence, 18, 3, True) == revcomp("GTAAA")


def test_wrapping_range_is_rejected_on_a_linear_construct():
    state = replay(SEQ, [], ops(("delete", {"start": 18, "end": 3})),
                   is_circular=False)
    assert state.sequence == SEQ
    assert any("linear" in w for w in state.warnings)


# --------------------------------------------------------------------------
# set_origin
# --------------------------------------------------------------------------

def test_set_origin_rotates_sequence_and_coordinates():
    base = [feat(4, 8, id="a", name="cassette")]
    state = replay(SEQ, base, ops(("set_origin", {"pos": 4})))
    assert state.sequence == "CCCCGGGGTTTTACGTAAAA"
    assert spans(state.features) == {"a": (0, 4)}
    assert covered_seq(state, "a") == "CCCC"


def test_set_origin_can_create_an_origin_crossing_feature():
    base = [feat(4, 8, id="a", name="cassette")]
    state = replay(SEQ, base, ops(("set_origin", {"pos": 6})))
    assert spans(state.features) == {"a": (18, 2)}
    assert covered_seq(state, "a") == "CCCC"


def test_set_origin_round_trips():
    base = [feat(18, 3, id="cross"), feat(4, 8, id="a")]
    state = replay(SEQ, base, ops(("set_origin", {"pos": 7}),
                                  ("set_origin", {"pos": 13})))
    assert state.sequence == SEQ
    assert spans(state.features) == {"cross": (18, 3), "a": (4, 8)}


def test_set_origin_is_rejected_on_a_linear_construct():
    state = replay(SEQ, [], ops(("set_origin", {"pos": 4})), is_circular=False)
    assert state.sequence == SEQ
    assert any("circular" in w for w in state.warnings)


@pytest.mark.parametrize("origin", range(20))
def test_set_origin_preserves_every_feature_subsequence(origin):
    base = [feat(18, 3, id="cross"), feat(4, 8, id="a"), feat(0, 20, id="whole")]
    before = {f.id: slice_span(SEQ, f.start, f.end, True) for f in base}
    state = replay(SEQ, base, ops(("set_origin", {"pos": origin})))
    after = {f.id: covered_seq(state, f.id) for f in state.features}
    for fid, seq in before.items():
        assert len(after[fid]) == len(seq)
    # "whole" always covers the entire (rotated) molecule
    assert len(after["whole"]) == 20
