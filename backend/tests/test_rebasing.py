"""One test per coordinate-rebasing rule in the spec.

These exercise the rebasing helpers directly; ``test_replay.py`` covers the
same rules through the public :func:`replay` entry point.
"""

from __future__ import annotations

import pytest

from app.domain.replay import (
    rebase_delete,
    rebase_insert,
    rebase_revcomp,
    rebase_rotate,
)
from tests.conftest import feat, spans

LIN = False
CIRC = True


# --------------------------------------------------------------------------
# insert at `pos`
# --------------------------------------------------------------------------

def test_insert_shifts_feature_that_starts_at_or_after_pos():
    fs, warns = rebase_insert([feat(100, 200, id="a")], pos=50, ins_len=10,
                              length=1000, is_circular=LIN)
    assert spans(fs) == {"a": (110, 210)}
    assert warns == []


def test_insert_at_exactly_the_feature_start_pushes_the_whole_feature():
    fs, _ = rebase_insert([feat(100, 200, id="a")], pos=100, ins_len=10,
                          length=1000, is_circular=LIN)
    assert spans(fs) == {"a": (110, 210)}


def test_insert_strictly_inside_a_feature_only_grows_its_end():
    fs, _ = rebase_insert([feat(100, 200, id="a")], pos=150, ins_len=10,
                          length=1000, is_circular=LIN)
    assert spans(fs) == {"a": (100, 210)}


def test_insert_at_exactly_the_feature_end_leaves_it_alone():
    fs, _ = rebase_insert([feat(100, 200, id="a")], pos=200, ins_len=10,
                          length=1000, is_circular=LIN)
    assert spans(fs) == {"a": (100, 200)}


def test_insert_after_a_feature_leaves_it_alone():
    fs, _ = rebase_insert([feat(100, 200, id="a")], pos=500, ins_len=10,
                          length=1000, is_circular=LIN)
    assert spans(fs) == {"a": (100, 200)}


def test_insert_does_not_flag_anything_as_truncated():
    fs, _ = rebase_insert([feat(100, 200, id="a")], pos=150, ins_len=10,
                          length=1000, is_circular=LIN)
    assert fs[0].truncated is False


# --------------------------------------------------------------------------
# delete [start, end)
# --------------------------------------------------------------------------

def test_delete_removes_a_feature_that_is_entirely_inside_the_range():
    fs, warns = rebase_delete([feat(100, 200, id="a", name="gfp")], 50, 300,
                              length=1000, is_circular=LIN)
    assert fs == []
    assert any("gfp" in w and "delete" in w for w in warns)


def test_delete_removes_a_feature_matching_the_range_exactly():
    fs, warns = rebase_delete([feat(100, 200, id="a", name="gfp")], 100, 200,
                              length=1000, is_circular=LIN)
    assert fs == []
    assert warns


def test_delete_shifts_a_feature_that_is_entirely_downstream():
    fs, warns = rebase_delete([feat(500, 600, id="a")], 100, 200,
                              length=1000, is_circular=LIN)
    assert spans(fs) == {"a": (400, 500)}
    assert warns == []
    assert fs[0].truncated is False


def test_delete_leaves_an_upstream_feature_untouched():
    fs, _ = rebase_delete([feat(10, 60, id="a")], 100, 200,
                          length=1000, is_circular=LIN)
    assert spans(fs) == {"a": (10, 60)}


def test_delete_abutting_the_feature_end_does_not_truncate_it():
    fs, _ = rebase_delete([feat(100, 200, id="a")], 200, 300,
                          length=1000, is_circular=LIN)
    assert spans(fs) == {"a": (100, 200)}
    assert fs[0].truncated is False


def test_delete_overlapping_the_feature_head_clips_and_marks_truncated():
    # delete [150, 250) chews the first 50 bp off feature [150, 300)
    fs, warns = rebase_delete([feat(150, 300, id="a")], 150, 250,
                              length=1000, is_circular=LIN)
    assert spans(fs) == {"a": (150, 200)}
    assert fs[0].truncated is True
    assert warns


def test_delete_overlapping_the_feature_tail_clips_and_marks_truncated():
    fs, warns = rebase_delete([feat(100, 200, id="a")], 150, 250,
                              length=1000, is_circular=LIN)
    assert spans(fs) == {"a": (100, 150)}
    assert fs[0].truncated is True
    assert warns


def test_delete_strictly_inside_a_feature_shrinks_it():
    fs, _ = rebase_delete([feat(100, 200, id="a")], 120, 140,
                          length=1000, is_circular=LIN)
    assert spans(fs) == {"a": (100, 180)}
    assert fs[0].truncated is True


def test_delete_rebases_several_features_independently():
    fs, _ = rebase_delete(
        [feat(0, 50, id="before"), feat(60, 90, id="inside"),
         feat(80, 200, id="overlap"), feat(300, 400, id="after")],
        55, 95, length=1000, is_circular=LIN,
    )
    assert spans(fs) == {
        "before": (0, 50),
        "overlap": (55, 160),
        "after": (260, 360),
    }


# --------------------------------------------------------------------------
# delete against a feature that crosses the origin
# --------------------------------------------------------------------------

def test_delete_inside_the_tail_of_an_origin_crossing_feature():
    # 5000 bp plasmid, feature 4900..4999 + 0..119 (220 bp)
    fs, _ = rebase_delete([feat(4900, 120, id="a")], 4950, 4980,
                          length=5000, is_circular=CIRC)
    assert spans(fs) == {"a": (4900, 120)}   # still wraps, now 190 bp
    assert fs[0].truncated is True


def test_delete_inside_the_head_of_an_origin_crossing_feature():
    fs, _ = rebase_delete([feat(4900, 120, id="a")], 50, 80,
                          length=5000, is_circular=CIRC)
    assert spans(fs) == {"a": (4870, 90)}
    assert fs[0].truncated is True


def test_delete_wiping_the_head_leaves_a_plain_linear_feature():
    fs, _ = rebase_delete([feat(4900, 120, id="a")], 0, 120,
                          length=5000, is_circular=CIRC)
    assert spans(fs) == {"a": (4780, 4880)}
    assert fs[0].truncated is True


def test_delete_far_from_an_origin_crossing_feature_only_shifts_its_tail():
    fs, warns = rebase_delete([feat(4900, 120, id="a")], 1000, 1100,
                              length=5000, is_circular=CIRC)
    assert spans(fs) == {"a": (4800, 120)}
    assert warns == []
    assert fs[0].truncated is False


# --------------------------------------------------------------------------
# revcomp_region [rstart, rend)
# --------------------------------------------------------------------------

def test_revcomp_mirrors_a_contained_feature_and_flips_its_strand():
    fs, warns = rebase_revcomp([feat(12, 15, id="a", strand=1)], 10, 20,
                               length=100, is_circular=LIN)
    assert spans(fs) == {"a": (15, 18)}
    assert fs[0].strand == -1
    assert warns == []


def test_revcomp_uses_the_spec_formula():
    # new_start = rstart + (rend - old_end)
    fs, _ = rebase_revcomp([feat(100, 250, id="a")], 100, 400,
                           length=1000, is_circular=LIN)
    assert spans(fs) == {"a": (250, 400)}


def test_revcomp_flips_a_minus_strand_feature_to_plus():
    fs, _ = rebase_revcomp([feat(12, 15, id="a", strand=-1)], 10, 20,
                           length=100, is_circular=LIN)
    assert fs[0].strand == 1


def test_revcomp_leaves_features_outside_the_range_untouched():
    fs, warns = rebase_revcomp([feat(50, 60, id="a", strand=1)], 10, 20,
                               length=100, is_circular=LIN)
    assert spans(fs) == {"a": (50, 60)}
    assert fs[0].strand == 1
    assert warns == []


def test_revcomp_warns_and_skips_a_feature_straddling_the_range_border():
    fs, warns = rebase_revcomp([feat(15, 40, id="a", name="ori", strand=1)],
                               10, 20, length=100, is_circular=LIN)
    assert spans(fs) == {"a": (15, 40)}
    assert fs[0].strand == 1
    assert any("ori" in w for w in warns)


def test_revcomp_of_the_whole_circle_mirrors_an_origin_crossing_feature():
    fs, warns = rebase_revcomp([feat(4900, 120, id="a")], 0, 5000,
                               length=5000, is_circular=CIRC)
    assert spans(fs) == {"a": (4880, 100)}
    assert fs[0].strand == -1
    assert warns == []


# --------------------------------------------------------------------------
# set_origin(pos)
# --------------------------------------------------------------------------

def test_rotate_shifts_a_downstream_feature_down():
    fs = rebase_rotate([feat(300, 400, id="a")], origin=100, length=5000)
    assert spans(fs) == {"a": (200, 300)}


def test_rotate_makes_a_straddled_feature_cross_the_origin():
    fs = rebase_rotate([feat(100, 200, id="a")], origin=150, length=5000)
    assert spans(fs) == {"a": (4950, 50)}


def test_rotate_onto_a_wrapping_feature_start_makes_it_linear():
    fs = rebase_rotate([feat(4900, 120, id="a")], origin=4900, length=5000)
    assert spans(fs) == {"a": (0, 220)}


def test_rotate_by_zero_is_identity():
    fs = rebase_rotate([feat(4900, 120, id="a"), feat(10, 20, id="b")],
                       origin=0, length=5000)
    assert spans(fs) == {"a": (4900, 120), "b": (10, 20)}


def test_rotate_keeps_a_full_length_feature_full_length():
    fs = rebase_rotate([feat(0, 5000, id="a")], origin=100, length=5000)
    assert fs[0].start == fs[0].end == 4900


@pytest.mark.parametrize("origin", [0, 1, 137, 2500, 4999])
def test_rotate_preserves_feature_length(origin):
    from app.domain.circular import span_length

    fs = rebase_rotate([feat(4900, 120, id="a"), feat(300, 900, id="b")],
                       origin=origin, length=5000)
    lengths = {f.id: span_length(f.start, f.end, 5000, True) for f in fs}
    assert lengths == {"a": 220, "b": 600}
