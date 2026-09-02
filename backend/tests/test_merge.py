"""Merging two operation logs: coordinate rebasing and the biological gate."""

from __future__ import annotations

import pytest

from app.domain.merge import (
    merge_logs,
    spans_overlap,
    strictly_inside,
    transforms_for,
)
from app.domain.models import ConstructState, Feature
from app.domain.replay import replay
from tests.conftest import op

PLASMID = "ACGTACGTACGTACGTACGT"  # 20 bp, circular

#  ATG + 4 Pro codons + TAA. Two clean inserts will combine into a stop.
CDS = "ATG" + "CCC" * 4 + "TAA"
CODING_PLASMID = CDS + "GGG" * 10  # 48 bp
CDS_FEATURE = [Feature(id="cds", name="gfp", kind="CDS", start=0, end=18)]


def logs(ancestor=(), target=(), branch=()):
    """Three op lists with the indices a real fork would have produced."""
    ancestors = [op(k, index=i, **p) for i, (k, p) in enumerate(ancestor)]
    n = len(ancestors)
    return (
        ancestors,
        [op(k, index=n + i, **p) for i, (k, p) in enumerate(target)],
        [op(k, index=n + i, **p) for i, (k, p) in enumerate(branch)],
    )


def do_merge(sequence, features=(), *, ancestor=(), target=(), branch=(),
             is_circular=True):
    a, t, b = logs(ancestor, target, branch)
    return merge_logs(
        sequence, list(features), a, t, b,
        is_circular=is_circular, construct_id="c",
    )


def merged_sequence(result) -> str:
    assert result.merged_state is not None, result.conflicts
    return result.merged_state.sequence


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

def test_spans_overlap_linear():
    assert spans_overlap(0, 10, 5, 15, 100, False)
    assert not spans_overlap(0, 10, 10, 20, 100, False)  # abutting, not sharing
    assert not spans_overlap(0, 10, 50, 60, 100, False)


def test_spans_overlap_across_the_origin():
    #  [95, 5) covers 95..99 and 0..4
    assert spans_overlap(95, 5, 0, 3, 100, True)
    assert spans_overlap(95, 5, 97, 99, 100, True)
    assert not spans_overlap(95, 5, 10, 90, 100, True)


def test_strictly_inside_linear_and_wrapping():
    assert strictly_inside(5, 0, 10, 100, False)
    assert not strictly_inside(0, 0, 10, 100, False)  # the leading edge
    assert not strictly_inside(10, 0, 10, 100, False)  # the trailing edge
    # A wrapping range contains the junction at position 0.
    assert strictly_inside(0, 95, 5, 100, True)
    assert strictly_inside(97, 95, 5, 100, True)
    assert not strictly_inside(95, 95, 5, 100, True)
    assert not strictly_inside(50, 95, 5, 100, True)


# --------------------------------------------------------------------------
# decomposing operations into transforms
# --------------------------------------------------------------------------

@pytest.fixture
def state():
    return ConstructState(sequence=PLASMID, is_circular=True)


def kinds(steps):
    return [type(s).__name__ for s in steps]


def test_transforms_for_each_operation_kind(state):
    assert kinds(transforms_for(op("insert", pos=3, seq="AA"), state)) == ["_Insert"]
    assert kinds(transforms_for(op("delete", start=3, end=7), state)) == ["_Delete"]
    assert kinds(transforms_for(op("replace", start=3, end=7, seq="A"), state)) == [
        "_Delete", "_Insert",
    ]
    assert kinds(transforms_for(op("revcomp_region", start=3, end=7), state)) == [
        "_Revcomp",
    ]
    assert kinds(transforms_for(op("set_origin", pos=4), state)) == ["_Rotate"]
    assert transforms_for(op("remove_feature", feature_id="x"), state) == []


def test_a_wrapping_range_is_rotated_to_the_front(state):
    assert kinds(transforms_for(op("delete", start=18, end=3), state)) == [
        "_Rotate", "_Delete",
    ]
    # revcomp preserves length, so it rotates back and outside coordinates hold
    assert kinds(transforms_for(op("revcomp_region", start=18, end=3), state)) == [
        "_Rotate", "_Revcomp", "_Rotate",
    ]


# --------------------------------------------------------------------------
# clean rebases
# --------------------------------------------------------------------------

def test_a_downstream_branch_delete_shifts_past_an_upstream_target_insert():
    result = do_merge(
        PLASMID,
        target=[("insert", {"pos": 2, "seq": "TTT"})],
        branch=[("delete", {"start": 10, "end": 14})],
    )
    assert result.clean
    (rebased,) = result.rebased
    assert rebased.payload == {"start": 13, "end": 17}
    #  TTT goes in at 2; the branch still removes ancestor bases 10..13.
    assert merged_sequence(result) == (
        PLASMID[:2] + "TTT" + PLASMID[2:10] + PLASMID[14:]
    )


def test_an_upstream_branch_edit_is_left_where_it_is():
    result = do_merge(
        PLASMID,
        target=[("insert", {"pos": 15, "seq": "TTT"})],
        branch=[("delete", {"start": 2, "end": 6})],
    )
    assert result.clean
    assert result.rebased[0].payload == {"start": 2, "end": 6}


def test_a_branch_edit_after_a_target_delete_moves_back():
    result = do_merge(
        PLASMID,
        target=[("delete", {"start": 0, "end": 4})],
        branch=[("insert", {"pos": 12, "seq": "AA"})],
    )
    assert result.clean
    assert result.rebased[0].payload == {"pos": 8, "seq": "AA"}


def test_the_merged_sequence_carries_both_edits():
    result = do_merge(
        PLASMID,
        target=[("delete", {"start": 0, "end": 4})],
        branch=[("delete", {"start": 16, "end": 20})],
    )
    assert merged_sequence(result) == PLASMID[4:16]


def test_operations_are_indexed_to_continue_the_target_log():
    result = do_merge(
        PLASMID,
        ancestor=[("insert", {"pos": 0, "seq": "A"})],
        target=[("insert", {"pos": 0, "seq": "C"})],
        branch=[("delete", {"start": 15, "end": 18})],
    )
    assert [o.index for o in result.rebased] == [2]


@pytest.mark.parametrize("swap", [False, True])
def test_well_separated_edits_merge_the_same_way_from_either_side(swap):
    """Order-independence: with no overlap, which side is the target is
    irrelevant to the resulting molecule."""
    a = [("delete", {"start": 1, "end": 4})]
    b = [("insert", {"pos": 15, "seq": "TTTT"})]
    target, branch = (b, a) if swap else (a, b)
    result = do_merge(PLASMID, target=target, branch=branch)
    assert result.clean
    assert merged_sequence(result) == (
        PLASMID[:1] + PLASMID[4:15] + "TTTT" + PLASMID[15:]
    )


# --------------------------------------------------------------------------
# the frame has to be carried across each branch operation
# --------------------------------------------------------------------------

def test_a_second_branch_operation_is_rebased_from_its_own_frame():
    """Regression: branch op 1 is written against the branch *after* op 0.

    Rebasing it through the target's transforms alone puts it in the wrong
    place — here it would land inside the "TT" the target inserted, rather
    than in front of the ancestor base it was aimed at.
    """
    result = do_merge(
        PLASMID,
        target=[("insert", {"pos": 5, "seq": "TT"})],
        branch=[
            ("insert", {"pos": 0, "seq": "GG"}),   # shifts the branch's own frame
            ("insert", {"pos": 6, "seq": "AAA"}),  # ancestor position 4
        ],
    )
    assert result.clean
    assert [o.payload["pos"] for o in result.rebased] == [0, 6]
    #  GG at the front, AAA in front of ancestor base 4, TT untouched at 5.
    assert merged_sequence(result) == (
        "GG" + PLASMID[:4] + "AAA" + PLASMID[4:5] + "TT" + PLASMID[5:]
    )


def test_carrying_the_frame_also_works_across_a_branch_delete():
    """The branch's own delete moves the target's insert *backwards*.

    Position 15 in the branch's frame is chosen so the two readings disagree:
    carried correctly the target's insert sits at 14 and the branch's lands
    after it, at 17; read against the uncarried insert at 18 it would stay at
    15, in front of the target's bases instead of behind them.
    """
    result = do_merge(
        PLASMID,
        target=[("insert", {"pos": 18, "seq": "TT"})],
        branch=[
            ("delete", {"start": 0, "end": 4}),
            ("insert", {"pos": 15, "seq": "CC"}),  # ancestor position 19
        ],
    )
    assert result.clean
    assert [o.payload for o in result.rebased] == [
        {"start": 0, "end": 4},
        {"pos": 17, "seq": "CC"},
    ]
    #  bases 0..3 gone, TT still before ancestor base 18, CC after base 18.
    assert merged_sequence(result) == (
        PLASMID[4:18] + "TT" + PLASMID[18:19] + "CC" + PLASMID[19:]
    )


def test_a_branch_set_origin_carries_the_targets_edit_around_the_molecule():
    result = do_merge(
        PLASMID,
        target=[("delete", {"start": 0, "end": 4})],
        branch=[
            ("set_origin", {"pos": 10}),
            ("insert", {"pos": 0, "seq": "AAAA"}),
        ],
    )
    assert result.clean
    assert result.merged_state is not None


# --------------------------------------------------------------------------
# conflicts
# --------------------------------------------------------------------------

def test_two_edits_to_the_same_bases_conflict():
    result = do_merge(
        PLASMID,
        target=[("delete", {"start": 4, "end": 10})],
        branch=[("delete", {"start": 8, "end": 14})],
    )
    assert result.has_conflicts
    (conflict,) = result.conflicts
    assert conflict.reason == "overlapping_edit"
    assert result.merged_state is None


def test_a_target_insert_inside_a_range_the_branch_removes_conflicts():
    result = do_merge(
        PLASMID,
        target=[("insert", {"pos": 8, "seq": "TTT"})],
        branch=[("delete", {"start": 4, "end": 12})],
    )
    assert [c.reason for c in result.conflicts] == ["overlapping_edit"]


def test_inserting_into_bases_the_target_deleted_conflicts():
    result = do_merge(
        PLASMID,
        target=[("delete", {"start": 4, "end": 12})],
        branch=[("insert", {"pos": 8, "seq": "TTT"})],
    )
    assert [c.reason for c in result.conflicts] == ["position_removed"]


def test_inserting_at_the_edge_of_a_deleted_range_is_not_a_conflict():
    result = do_merge(
        PLASMID,
        target=[("delete", {"start": 4, "end": 12})],
        branch=[("insert", {"pos": 12, "seq": "TTT"})],
    )
    assert result.clean


def test_two_inserts_at_the_same_position_are_ordered_not_conflicted():
    result = do_merge(
        PLASMID,
        target=[("insert", {"pos": 8, "seq": "TTT"})],
        branch=[("insert", {"pos": 8, "seq": "AAA"})],
    )
    assert result.clean
    # The target holds the earlier slot; the branch's bases follow.
    assert merged_sequence(result) == (
        PLASMID[:8] + "TTT" + "AAA" + PLASMID[8:]
    )


def test_a_revcomp_overlapping_the_branch_edit_conflicts():
    result = do_merge(
        PLASMID,
        target=[("revcomp_region", {"start": 4, "end": 12})],
        branch=[("delete", {"start": 8, "end": 16})],
    )
    assert [c.reason for c in result.conflicts] == ["overlapping_edit"]


def test_a_revcomp_elsewhere_leaves_the_branch_edit_alone():
    result = do_merge(
        PLASMID,
        target=[("revcomp_region", {"start": 0, "end": 8})],
        branch=[("delete", {"start": 12, "end": 16})],
    )
    assert result.clean
    assert result.rebased[0].payload == {"start": 12, "end": 16}


def test_updating_a_feature_the_target_removed_conflicts():
    features = [Feature(id="f", name="gfp", start=2, end=8)]
    result = do_merge(
        PLASMID, features,
        target=[("remove_feature", {"feature_id": "f"})],
        branch=[("update_feature", {"feature_id": "f", "patch": {"name": "eGFP"}})],
    )
    assert [c.reason for c in result.conflicts] == ["feature_removed"]


def test_removing_a_feature_both_sides_removed_is_not_a_conflict():
    features = [Feature(id="f", name="gfp", start=2, end=8)]
    result = do_merge(
        PLASMID, features,
        target=[("remove_feature", {"feature_id": "f"})],
        branch=[("remove_feature", {"feature_id": "f"})],
    )
    assert result.clean
    assert result.rebased == []
    assert [s.reason for s in result.skipped] == ["already_applied"]


def test_an_added_feature_is_rebased_across_the_targets_edit():
    result = do_merge(
        PLASMID,
        target=[("insert", {"pos": 0, "seq": "TTTT"})],
        branch=[("add_feature", {"feature": {
            "id": "new", "name": "tag", "kind": "CDS", "start": 8, "end": 14,
        }})],
    )
    assert result.clean
    feature = result.rebased[0].payload["feature"]
    assert (feature["start"], feature["end"]) == (12, 18)


# --------------------------------------------------------------------------
# origin-crossing merges
# --------------------------------------------------------------------------

def test_a_branch_edit_survives_a_target_edit_across_the_origin():
    result = do_merge(
        PLASMID,
        target=[("delete", {"start": 18, "end": 2})],  # wraps
        branch=[("delete", {"start": 8, "end": 12})],
    )
    assert result.clean
    # After the wrapping delete the origin moves to the old position 2,
    # so the branch's range moves back by that much.
    assert result.rebased[0].payload == {"start": 6, "end": 10}
    rotated = PLASMID[2:18]  # what the wrapping delete leaves, re-origined
    assert merged_sequence(result) == rotated[:6] + rotated[10:]


def test_an_edit_the_wrapping_delete_consumed_conflicts():
    result = do_merge(
        PLASMID,
        target=[("delete", {"start": 18, "end": 2})],
        branch=[("delete", {"start": 0, "end": 2})],
    )
    assert result.has_conflicts


def test_a_target_rotation_never_conflicts_and_just_moves_coordinates():
    result = do_merge(
        PLASMID,
        target=[("set_origin", {"pos": 5})],
        branch=[("delete", {"start": 8, "end": 12})],
    )
    assert result.clean
    assert result.rebased[0].payload == {"start": 3, "end": 7}


# --------------------------------------------------------------------------
# the biological gate
# --------------------------------------------------------------------------

def test_two_clean_edits_can_combine_into_a_premature_stop():
    """The whole point of the exercise.

    Neither branch breaks the protein and the two inserts do not overlap, so a
    text-level merge succeeds. Read as codons, the merged sequence stops at
    codon 3.
    """
    result = do_merge(
        CODING_PLASMID, CDS_FEATURE,
        target=[("insert", {"pos": 4, "seq": "CCT"})],
        branch=[("insert", {"pos": 4, "seq": "AAG"})],
    )
    assert not result.has_conflicts       # coordinates merge cleanly ...
    assert result.breaks_biology          # ... and the protein is gone
    assert not result.clean

    (issue,) = result.new_frame_issues
    assert issue.feature_name == "gfp"
    assert issue.problem == "premature_stop"
    assert issue.codon == 3

    # Each branch on its own is fine.
    from app.domain.analysis import check_reading_frames
    for edit in ("CCT", "AAG"):
        alone = replay(
            CODING_PLASMID, CDS_FEATURE,
            [op("insert", index=0, pos=4, seq=edit)],
        )
        assert check_reading_frames(alone) == []


def test_a_break_that_predates_the_merge_is_not_blamed_on_it():
    """The branch was already frameshifted; merging did not cause that."""
    result = do_merge(
        CODING_PLASMID, CDS_FEATURE,
        target=[("insert", {"pos": 30, "seq": "A"})],   # outside the CDS
        branch=[("insert", {"pos": 4, "seq": "T"})],    # frameshift on the branch
    )
    assert not result.has_conflicts
    assert not result.breaks_biology
    assert result.new_frame_issues == []


def test_in_frame_edits_on_both_sides_stay_in_frame():
    result = do_merge(
        CODING_PLASMID, CDS_FEATURE,
        target=[("insert", {"pos": 3, "seq": "GGG"})],
        branch=[("insert", {"pos": 15, "seq": "GAG"})],
    )
    assert result.clean
    assert result.new_frame_issues == []


def test_a_merge_can_restore_a_frame_each_branch_broke():
    """Frameshift is additive modulo 3, so two 1 bp inserts cancel a third."""
    result = do_merge(
        CODING_PLASMID, CDS_FEATURE,
        target=[("insert", {"pos": 3, "seq": "GG"})],
        branch=[("insert", {"pos": 15, "seq": "G"})],
    )
    assert result.clean
    assert result.new_frame_issues == []


def test_non_coding_features_do_not_gate_a_merge():
    features = [Feature(id="p", name="Plac", kind="promoter", start=0, end=18)]
    result = do_merge(
        CODING_PLASMID, features,
        target=[("insert", {"pos": 4, "seq": "CCT"})],
        branch=[("insert", {"pos": 4, "seq": "AAG"})],
    )
    assert result.clean


def test_conflicts_short_circuit_the_biological_check():
    result = do_merge(
        CODING_PLASMID, CDS_FEATURE,
        target=[("delete", {"start": 4, "end": 10})],
        branch=[("delete", {"start": 8, "end": 14})],
    )
    assert result.has_conflicts
    assert result.new_frame_issues == []
    assert result.merged_state is None
