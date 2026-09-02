"""Diffing two derived states."""

from __future__ import annotations

import random
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from app.domain.diff import (
    MAX_SEGMENT_BASES,
    diff_features,
    diff_sequences,
    diff_states,
    find_origin_shift,
)
from app.domain.models import ConstructState
from app.domain.replay import replay
from app.domain.seqio import parse_sequence_file
from tests.conftest import feat, ops

DATA = Path(__file__).parents[1] / "data"


def state(sequence: str, features=(), *, is_circular: bool = True):
    return ConstructState(
        sequence=sequence, features=list(features), is_circular=is_circular
    )


def tags(diff):
    return [s.op for s in diff.segments]


# --------------------------------------------------------------------------
# the difflib trap
# --------------------------------------------------------------------------

def test_autojunk_would_wreck_a_four_letter_alphabet():
    """Why `autojunk=False` is not optional here.

    difflib's default drops elements that appear in more than 1% of positions.
    Every base in a DNA sequence does, so the heuristic junks the entire
    alphabet and the alignment collapses.
    """
    rng = random.Random(7)
    left = "".join(rng.choice("ACGT") for _ in range(2000))
    right = left[:800] + "GGGGGGGGGG" + left[850:]

    crippled = SequenceMatcher(None, left, right).ratio()
    assert crippled < 0.5

    diff = diff_sequences(left, right, False)
    assert diff.identity > 0.97


def test_a_single_edit_in_a_long_sequence_is_localised():
    rng = random.Random(11)
    left = "".join(rng.choice("ACGT") for _ in range(2000))
    right = left[:800] + "GGGGGGGGGG" + left[850:]

    diff = diff_sequences(left, right, False)
    (change,) = diff.changed_segments
    assert change.op == "replace"
    #  The exact border floats by a base or two: difflib is free to match a
    #  base the replacement happens to share with its neighbour.
    assert 795 <= change.left_start <= 805
    assert change.left_end == 850
    assert set(change.right_seq) == {"G"}
    assert diff.bases_removed + diff.bases_added <= 60
    assert diff.identity > 0.97


def test_changes_split_by_a_few_matching_bases_are_reported_as_one():
    """difflib aligns characters; a reader wants the biological change.

    Replacing 50 bases with a run of G's lets the matcher pick up stray G's
    either side, fragmenting one edit into three opcodes.
    """
    rng = random.Random(11)
    left = "".join(rng.choice("ACGT") for _ in range(2000))
    right = left[:800] + "GGGGGGGGGG" + left[850:]

    raw = SequenceMatcher(None, left, right, autojunk=False).get_opcodes()
    assert len([c for c in raw if c[0] != "equal"]) > 1
    assert len(diff_sequences(left, right, False).changed_segments) == 1


def test_a_long_matching_run_is_not_absorbed():
    left = "AAAACCCC" + "G" * 40 + "TTTTCCCC"
    right = "TTTTCCCC" + "G" * 40 + "AAAACCCC"
    diff = diff_sequences(left, right, False)
    assert any(s.op == "equal" for s in diff.segments)
    assert len(diff.changed_segments) >= 2


# --------------------------------------------------------------------------
# sequence diff
# --------------------------------------------------------------------------

def test_identical_sequences():
    diff = diff_sequences("ACGTACGTAC", "ACGTACGTAC", False)
    assert diff.identical
    assert diff.identity == 1.0
    assert tags(diff) == ["equal"]
    assert diff.changed_segments == []


def test_a_pure_insertion():
    diff = diff_sequences("ACGTACGT", "ACGTTTTACGT", False)
    (change,) = diff.changed_segments
    assert change.op == "insert"
    assert change.right_seq == "TTT"
    assert (diff.bases_added, diff.bases_removed) == (3, 0)


def test_a_pure_deletion():
    diff = diff_sequences("ACGTTTTACGT", "ACGTACGT", False)
    (change,) = diff.changed_segments
    assert change.op == "delete"
    assert change.left_seq == "TTT"
    assert (diff.bases_added, diff.bases_removed) == (0, 3)


def test_changed_blocks_carry_the_bases_and_equal_blocks_do_not():
    diff = diff_sequences("ACGTACGTAC", "ACGTGGGTAC", False)
    equal = [s for s in diff.segments if s.op == "equal"]
    assert all(s.left_seq == "" and s.right_seq == "" for s in equal)
    assert all(s.left_seq or s.right_seq for s in diff.changed_segments)


def test_a_huge_change_is_clipped_and_flagged():
    left = "A" * 10
    right = "A" * 10 + "C" * (MAX_SEGMENT_BASES + 50)
    (change,) = diff_sequences(left, right, False).changed_segments
    assert change.truncated is True
    assert len(change.right_seq) == MAX_SEGMENT_BASES
    # The coordinates still describe the whole block.
    assert change.right_end - change.right_start == MAX_SEGMENT_BASES + 50


# --------------------------------------------------------------------------
# circular origin normalisation
# --------------------------------------------------------------------------

_rng = random.Random(3)
PLASMID = "".join(_rng.choice("ACGT") for _ in range(221))
#: 200 bp of perfect ACGT repeat: rotating it by any multiple of 4 leaves the
#: first bases identical, which is what defeats probe-based origin anchoring.
REPETITIVE = "".join("ACGT"[i % 4] for i in range(200)) + "GGCATTACGATCAGGATCCTA"


def test_find_origin_shift_locates_a_rotation():
    rotated = PLASMID[60:] + PLASMID[:60]
    assert find_origin_shift(PLASMID, rotated, True) == len(PLASMID) - 60


def test_a_rotation_alone_is_reported_as_an_origin_shift_not_a_rewrite():
    rotated = PLASMID[60:] + PLASMID[:60]
    diff = diff_sequences(PLASMID, rotated, True)
    assert diff.identical is True
    assert diff.identity == 1.0
    assert diff.origin_shift == len(PLASMID) - 60
    assert diff.changed_segments == []


def test_the_same_rotation_on_a_linear_molecule_is_a_real_rearrangement():
    rotated = PLASMID[60:] + PLASMID[:60]
    diff = diff_sequences(PLASMID, rotated, False)
    assert diff.origin_shift == 0
    assert diff.changed_segments != []


def test_an_edit_survives_the_origin_normalisation():
    edited = PLASMID[:100] + "TTTTTT" + PLASMID[100:]
    rotated = edited[60:] + edited[:60]
    diff = diff_sequences(PLASMID, rotated, True)
    assert diff.origin_shift != 0
    assert diff.bases_added == 6 and diff.bases_removed == 0
    assert "T" * 6 in "".join(s.right_seq for s in diff.changed_segments)


@pytest.mark.parametrize(
    "left,right",
    [
        ("ACGTACGTACGTACGT", "ACGTACGTACGTACGT"),
        ("ACGTACGTACGTACGT", "ACGTTTTTACGTACGT"),
        ("ACGTACGTACGTACGT", ""),
        ("", "ACGTACGTACGTACGT"),
        ("AAAACCCCGGGGTTTT", "TTTTGGGGCCCCAAAA"),
    ],
)
def test_the_segments_always_tile_both_sequences(left, right):
    """Whatever the alignment, the blocks must cover each side exactly once."""
    diff = diff_sequences(left, right, False)
    assert "".join(left[s.left_start : s.left_end] for s in diff.segments) == left
    assert "".join(right[s.right_start : s.right_end] for s in diff.segments) == right


def test_a_rotation_is_found_even_when_the_origin_sits_in_a_repeat():
    """The case that rules out anchoring on a fixed probe from the start."""
    rotated = REPETITIVE[60:] + REPETITIVE[:60]   # 60 is a multiple of the period
    assert REPETITIVE[:30] == rotated[:30]        # a probe would see no shift
    diff = diff_sequences(REPETITIVE, rotated, True)
    assert diff.identical is True
    assert diff.origin_shift == len(REPETITIVE) - 60


def test_no_anchor_falls_back_to_the_plain_diff():
    assert find_origin_shift("ACGTACGT", "TTTTTTTT", True) == 0


# --------------------------------------------------------------------------
# feature diff
# --------------------------------------------------------------------------

def test_features_are_paired_by_id():
    before = [feat(10, 20, id="a", name="gfp"), feat(30, 40, id="b", name="ori")]
    after = [feat(15, 25, id="a", name="gfp"), feat(30, 40, id="b", name="ori")]
    diff = diff_features(before, after)
    assert diff.added == [] and diff.removed == []
    assert diff.unchanged == 1
    (change,) = diff.changed
    assert change.changed_fields == ["start", "end"]
    assert (change.before.start, change.after.start) == (10, 15)


def test_added_and_removed_features():
    before = [feat(10, 20, id="a", name="gfp")]
    after = [feat(30, 40, id="b", name="tag")]
    diff = diff_features(before, after)
    assert [f.name for f in diff.removed] == ["gfp"]
    assert [f.name for f in diff.added] == ["tag"]


def test_a_rename_is_a_change_not_an_add_and_a_remove():
    diff = diff_features(
        [feat(10, 20, id="a", name="gfp")], [feat(10, 20, id="a", name="eGFP")]
    )
    assert diff.added == [] and diff.removed == []
    assert diff.changed[0].changed_fields == ["name"]


def test_features_of_unrelated_constructs_pair_on_name_kind_and_strand():
    before = [feat(10, 20, id="x1", name="bla", kind="CDS", strand=-1)]
    after = [feat(90, 100, id="y9", name="bla", kind="CDS", strand=-1)]
    diff = diff_features(before, after)
    assert diff.added == [] and diff.removed == []
    assert diff.changed[0].changed_fields == ["start", "end"]


def test_the_truncation_flag_shows_up_as_a_change():
    after = feat(10, 15, id="a", name="gfp")
    after.truncated = True
    diff = diff_features([feat(10, 20, id="a", name="gfp")], [after])
    assert diff.changed[0].changed_fields == ["end", "truncated"]


# --------------------------------------------------------------------------
# whole states
# --------------------------------------------------------------------------

def test_diff_states_of_a_construct_against_itself_is_empty():
    record = parse_sequence_file((DATA / "puc19_annotated.gb").read_text(), "p.gb")
    current = state(record.sequence, record.features)
    diff = diff_states(current, current)
    assert diff.sequence.identical
    assert diff.features.unchanged == 18
    assert diff.features.added == diff.features.removed == []


def test_diff_states_after_a_delete_reports_both_halves():
    record = parse_sequence_file((DATA / "puc19_annotated.gb").read_text(), "p.gb")
    before = state(record.sequence, record.features)
    after = replay(
        record.sequence, record.features, ops(("delete", {"start": 200, "end": 700}))
    )

    diff = diff_states(before, after)
    (change,) = diff.sequence.changed_segments
    assert change.op == "delete"
    assert (change.left_start, change.left_end) == (200, 700)
    assert diff.sequence.bases_removed == 500

    names = {f.name for f in diff.features.removed}
    assert "lacZalpha" in names                  # wiped out by the deletion
    assert any(c.after.truncated for c in diff.features.changed)


def test_a_set_origin_shows_as_an_origin_shift_with_features_intact():
    record = parse_sequence_file((DATA / "puc19_annotated.gb").read_text(), "p.gb")
    before = state(record.sequence, record.features)
    after = replay(
        record.sequence, record.features, ops(("set_origin", {"pos": 1500}))
    )

    diff = diff_states(before, after)
    assert diff.sequence.identical
    assert diff.sequence.origin_shift == len(record.sequence) - 1500
    # Rotating the plasmid moved every coordinate, but nothing actually changed.
    assert diff.features.changed == []
    assert diff.features.unchanged == 18


@pytest.mark.parametrize("origin", [1, 137, 1500, 2685])
def test_rotation_is_never_reported_as_a_difference(origin):
    record = parse_sequence_file((DATA / "puc19_annotated.gb").read_text(), "p.gb")
    before = state(record.sequence, record.features)
    after = replay(
        record.sequence, record.features, ops(("set_origin", {"pos": origin}))
    )
    diff = diff_states(before, after)
    assert diff.sequence.identical
    assert diff.features.changed == []


# --------------------------------------------------------------------------
# displacement is not the same as change
# --------------------------------------------------------------------------

def test_a_feature_displaced_by_an_upstream_indel_is_shifted_not_changed():
    left = state("AAAACCCCGGGGTTTT", [feat(8, 12, id="a", name="gfp")])
    right = state("AAAATTCCCCGGGGTTTT", [feat(10, 14, id="a", name="gfp")])
    diff = diff_features(
        left.features, right.features,
        left_seq=left.sequence, right_seq=right.sequence, is_circular=True,
    )
    assert diff.changed == []
    (moved,) = diff.shifted
    assert moved.changed_fields == ["start", "end"]
    # It still covers the same bases, which is what makes it a shift.
    assert left.sequence[8:12] == right.sequence[10:14] == "GGGG"


def test_a_feature_over_different_bases_is_changed_even_at_the_same_length():
    left = state("AAAACCCCGGGGTTTT", [feat(4, 8, id="a", name="gfp")])
    right = state("AAAACCCCGGGGTTTT", [feat(8, 12, id="a", name="gfp")])
    diff = diff_features(
        left.features, right.features,
        left_seq=left.sequence, right_seq=right.sequence, is_circular=True,
    )
    assert diff.shifted == []
    assert diff.changed[0].changed_fields == ["start", "end"]


def test_a_rename_is_never_just_a_shift():
    left = state("AAAACCCCGGGGTTTT", [feat(8, 12, id="a", name="gfp")])
    right = state("AAAATTCCCCGGGGTTTT", [feat(10, 14, id="a", name="eGFP")])
    diff = diff_features(
        left.features, right.features,
        left_seq=left.sequence, right_seq=right.sequence, is_circular=True,
    )
    assert diff.shifted == []
    assert set(diff.changed[0].changed_fields) == {"name", "start", "end"}


def test_one_deletion_shifts_the_downstream_features_without_changing_them():
    record = parse_sequence_file((DATA / "puc19_annotated.gb").read_text(), "p.gb")
    before = state(record.sequence, record.features)
    after = replay(
        record.sequence, record.features, ops(("delete", {"start": 700, "end": 730}))
    )
    diff = diff_states(before, after)

    # Everything after position 730 moved back 30 bp but is otherwise intact.
    assert len(diff.features.shifted) > 5
    assert all(c.changed_fields == ["start", "end"] for c in diff.features.shifted)
    # Only the features the cut actually crossed count as changed.
    assert all(
        c.after.truncated or "truncated" in c.changed_fields
        for c in diff.features.changed
    )
