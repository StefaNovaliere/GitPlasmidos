"""End-to-end behaviour of the pure replay engine."""

from __future__ import annotations

import pytest

from app.domain.models import OperationError
from app.domain.replay import replay
from tests.conftest import feat, op, ops, spans

SEQ = "AAAACCCCGGGGTTTTACGT"


def test_replay_with_no_operations_returns_the_base():
    base = [feat(4, 8, id="a")]
    state = replay(SEQ, base, [])
    assert state.sequence == SEQ
    assert spans(state.features) == {"a": (4, 8)}
    assert state.warnings == []


def test_replay_does_not_mutate_the_base_features():
    base = [feat(4, 8, id="a")]
    replay(SEQ, base, ops(("insert", {"pos": 0, "seq": "TTT"})))
    assert (base[0].start, base[0].end) == (4, 8)


def test_replay_applies_operations_in_index_order_not_list_order():
    unordered = [
        op("insert", index=1, pos=0, seq="C"),
        op("insert", index=0, pos=0, seq="G"),
    ]
    state = replay("AAAA", [], unordered)
    # index 0 prepends G -> GAAAA ; index 1 prepends C -> CGAAAA
    assert state.sequence == "CGAAAA"


def test_replay_skips_reverted_operations():
    given = [
        op("insert", index=0, pos=0, seq="G"),
        op("insert", index=1, pos=0, seq="C", reverted=True),
    ]
    assert replay("AAAA", [], given).sequence == "GAAAA"


def test_gc_content():
    state = replay("GGCCAAAATTTT", [], [], is_circular=False)
    assert state.gc_content == pytest.approx(4 / 12)
    assert replay("", [], [], is_circular=False).gc_content == 0.0


def test_lowercase_input_is_normalised_to_uppercase():
    assert replay("acgt", [], [], is_circular=False).sequence == "ACGT"


# --------------------------------------------------------------------------
# acceptance criterion 2: downstream features land where hand arithmetic says
# --------------------------------------------------------------------------

def test_delete_puts_downstream_features_exactly_where_manual_maths_says():
    seq = "".join("ACGT"[i % 4] for i in range(1000))
    base = [
        feat(100, 200, id="promoter"),
        feat(250, 700, id="cds"),
        feat(800, 900, id="terminator"),
    ]
    # Delete [300, 450): 150 bp gone.
    state = replay(seq, base, ops(("delete", {"start": 300, "end": 450})))

    assert state.length == 1000 - 150
    # promoter is entirely upstream -> untouched
    # cds straddles the deletion -> 250..699 minus 300..449 = 300 bp, clipped
    # terminator is entirely downstream -> both ends move back by 150
    assert spans(state.features) == {
        "promoter": (100, 200),
        "cds": (250, 550),
        "terminator": (650, 750),
    }
    by_id = {f.id: f for f in state.features}
    assert by_id["cds"].truncated is True
    assert by_id["promoter"].truncated is False
    assert by_id["terminator"].truncated is False
    # the terminator still covers the same bases it did before
    assert state.sequence[650:750] == seq[800:900]


def test_delete_reports_the_feature_it_wiped_out():
    state = replay(SEQ, [feat(5, 8, id="a", name="lacZ")],
                   ops(("delete", {"start": 4, "end": 10})))
    assert state.features == []
    assert any("lacZ" in w for w in state.warnings)


# --------------------------------------------------------------------------
# acceptance criterion 3: undo restores the exact previous state
# --------------------------------------------------------------------------

def test_undo_restores_the_exact_state_including_truncation_flags():
    base = [feat(4, 12, id="a", name="cds"), feat(14, 18, id="b")]
    before = replay(SEQ, base, [])

    edit = ops(("delete", {"start": 8, "end": 16}))
    after = replay(SEQ, base, edit)
    assert spans(after.features) == {"a": (4, 8), "b": (8, 10)}
    assert {f.id: f.truncated for f in after.features} == {"a": True, "b": True}

    edit[0].reverted = True
    undone = replay(SEQ, base, edit)
    assert undone.model_dump() == before.model_dump()
    assert all(f.truncated is False for f in undone.features)


def test_undo_of_a_chain_replays_only_the_surviving_prefix():
    given = ops(
        ("insert", {"pos": 0, "seq": "GG"}),
        ("insert", {"pos": 0, "seq": "CC"}),
        ("insert", {"pos": 0, "seq": "TT"}),
    )
    assert replay("AAAA", [], given).sequence == "TTCCGGAAAA"
    given[2].reverted = True
    assert replay("AAAA", [], given).sequence == "CCGGAAAA"
    given[1].reverted = True
    assert replay("AAAA", [], given).sequence == "GGAAAA"
    given[1].reverted = False  # redo
    assert replay("AAAA", [], given).sequence == "CCGGAAAA"


# --------------------------------------------------------------------------
# replace is delete + insert
# --------------------------------------------------------------------------

def test_replace_equals_delete_then_insert():
    base = [feat(0, 4, id="up"), feat(16, 20, id="down")]
    via_replace = replay(SEQ, base,
                         ops(("replace", {"start": 8, "end": 12, "seq": "AAAAAA"})))
    via_pair = replay(SEQ, base, ops(("delete", {"start": 8, "end": 12}),
                                     ("insert", {"pos": 8, "seq": "AAAAAA"})))
    assert via_replace.sequence == via_pair.sequence
    assert spans(via_replace.features) == spans(via_pair.features)
    assert via_replace.sequence == "AAAACCCCAAAAAATTTTACGT"
    assert spans(via_replace.features) == {"up": (0, 4), "down": (18, 22)}


def test_replace_with_a_shorter_sequence_shrinks_the_construct():
    state = replay(SEQ, [], ops(("replace", {"start": 4, "end": 12, "seq": "G"})))
    assert state.sequence == "AAAAGTTTTACGT"


# --------------------------------------------------------------------------
# feature operations
# --------------------------------------------------------------------------

def test_add_remove_and_update_feature():
    given = ops(
        ("add_feature", {"feature": {"id": "x", "name": "gfp", "kind": "CDS",
                                     "start": 4, "end": 10, "strand": 1}}),
        ("update_feature", {"feature_id": "x",
                            "patch": {"name": "eGFP", "strand": -1}}),
    )
    state = replay(SEQ, [], given)
    assert [(f.id, f.name, f.strand) for f in state.features] == [("x", "eGFP", -1)]

    given.append(op("remove_feature", index=2, feature_id="x"))
    assert replay(SEQ, [], given).features == []


def test_added_feature_is_rebased_by_later_operations():
    given = ops(
        ("add_feature", {"feature": {"id": "x", "name": "gfp", "start": 10,
                                     "end": 16}}),
        ("insert", {"pos": 0, "seq": "TTTT"}),
    )
    assert spans(replay(SEQ, [], given).features) == {"x": (14, 20)}


def test_add_feature_out_of_bounds_is_reported_not_fatal():
    state = replay(SEQ, [], ops(("add_feature", {"feature": {
        "id": "x", "name": "oops", "start": 5, "end": 999}})))
    assert state.features == []
    assert any("out of range" in w for w in state.warnings)


def test_add_feature_crossing_the_origin_is_allowed_when_circular():
    given = ops(("add_feature", {"feature": {"id": "x", "name": "cross",
                                             "start": 18, "end": 3}}))
    assert spans(replay(SEQ, [], given).features) == {"x": (18, 3)}


def test_add_feature_crossing_the_origin_is_rejected_when_linear():
    given = ops(("add_feature", {"feature": {"id": "x", "name": "cross",
                                             "start": 18, "end": 3}}))
    state = replay(SEQ, [], given, is_circular=False)
    assert state.features == []
    assert state.warnings


# --------------------------------------------------------------------------
# error handling
# --------------------------------------------------------------------------

def test_broken_operation_is_downgraded_to_a_warning_by_default():
    state = replay(SEQ, [], ops(("delete", {"start": 5, "end": 500})))
    assert state.sequence == SEQ
    assert len(state.warnings) == 1


def test_strict_mode_raises_instead():
    with pytest.raises(OperationError):
        replay(SEQ, [], ops(("delete", {"start": 5, "end": 500})), strict=True)


def test_invalid_bases_are_rejected():
    from app.domain.models import SequenceError

    with pytest.raises(SequenceError):
        replay("ACGTXZ", [], [])


def test_iupac_ambiguity_codes_are_accepted():
    assert replay("ACGTNRYSWKMBDHV", [], []).length == 15


def test_deleting_the_whole_sequence_is_refused():
    with pytest.raises(OperationError):
        replay(SEQ, [], ops(("delete", {"start": 0, "end": 20})), strict=True)


def test_empty_range_is_refused():
    with pytest.raises(OperationError):
        replay(SEQ, [], ops(("delete", {"start": 5, "end": 5})), strict=True)
