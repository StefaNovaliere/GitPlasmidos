"""Suppressing a finding: the operation, the fingerprint, and the rebase.

A suppression is an edit, not metadata. It goes in the log like everything
else, which is what gives it undo, an author, a place in the diff and a
defined behaviour under merge.

The interesting question is what happens when the molecule moves underneath
it. Two mechanisms, deliberately independent:

* the ``(rule_id, feature_id)`` key holds the suppression to its finding, and
  survives every coordinate edit for free;
* the digest of the window the rule read decides whether the *evidence* still
  holds. Bases moving is not bases changing.
"""

from __future__ import annotations

import pytest

from app.domain.merge import merge_logs
from app.domain.models import ConstructState, Feature, OperationError
from app.domain.replay import replay
from app.domain.rules.engine import lint, suppression_payload
from app.domain.rules.models import Finding, Rule
from tests.conftest import op, ops

#: 60 bp: filler, then a bare window, then a CDS, then filler.
UPSTREAM = "A" * 20
WINDOW = "C" * 12
CDS = "ATGAAAGGGCCCTAA"  # 15 bp
TAIL = "T" * 13
SEQ = UPSTREAM + WINDOW + CDS + TAIL
CDS_FEATURE = Feature(id="cds", name="lacZalpha", kind="CDS", start=32, end=47)

RULE = Rule.model_validate(
    {
        "id": "rbs-demo",
        "title": "No ribosome binding site upstream of the start codon",
        "severity": "error",
        "target": {"feature_kind": "CDS"},
        "region": {"where": "upstream", "window": 12},
        "look": {"motif": "AGGAGG"},
        "expect": {"presence": "required"},
        "message": "{feature}: no Shine-Dalgarno in the {window} bases upstream",
        "evidence": {
            "citation": "doi:10.1073/pnas.71.4.1342",
            "organism": "Escherichia coli",
            "confidence": "established",
        },
        "examples": [{"name": "unused here", "sequence": "ACGT", "triggers": False}],
    }
)

#: Same rule asking a wider question. Its digest differs, so suppressions of
#: the narrow one must not carry over.
WIDER = Rule.model_validate(
    {**RULE.model_dump(), "region": {"where": "upstream", "window": 20}}
)

REASON = "este RBS es débil a propósito, estamos titulando expresión"


def state_of(*specs) -> ConstructState:
    return replay(SEQ, [CDS_FEATURE], ops(*specs), is_circular=True)


def only(state: ConstructState, rule: Rule = RULE) -> Finding:
    found = lint([rule], state)
    assert len(found) == 1, found
    return found[0]


def payload(reason: str = REASON) -> dict:
    """What the UI posts back: the finding's own evidence, plus a reason."""
    return suppression_payload(only(state_of()), reason)


# ---------------------------------------------------------------------------
# the operation
# ---------------------------------------------------------------------------

def test_a_finding_is_reported_before_anybody_silences_it():
    finding = only(state_of())
    assert finding.suppressed is False
    assert finding.blocking is True
    assert finding.window is not None
    assert (finding.window.start, finding.window.end) == (20, 32)
    assert finding.window.excerpt == WINDOW


def test_suppressing_marks_the_finding_and_never_drops_it():
    finding = only(state_of(("suppress_finding", payload())))
    assert finding.suppressed is True
    assert finding.suppression is not None
    assert finding.suppression.reason == REASON
    assert finding.suppression.stale is False
    # Still counted: "1 finding, 1 suppressed" is auditable, a hidden one rots.
    assert finding.blocking is False


def test_a_suppression_needs_a_reason():
    with pytest.raises(OperationError, match="reason"):
        replay(
            SEQ, [CDS_FEATURE], ops(("suppress_finding", payload(reason="  "))),
            is_circular=True, strict=True,
        )


def test_cannot_suppress_against_a_feature_that_is_not_there():
    body = payload() | {"feature_id": "ghost"}
    with pytest.raises(OperationError, match="no feature with id"):
        replay(
            SEQ, [CDS_FEATURE], ops(("suppress_finding", body)),
            is_circular=True, strict=True,
        )


def test_suppressing_twice_replaces_the_evidence_rather_than_stacking():
    state = state_of(
        ("suppress_finding", payload()),
        ("suppress_finding", payload(reason="second look, still deliberate")),
    )
    assert len(state.suppressions) == 1
    assert state.suppressions[0].reason == "second look, still deliberate"


def test_unsuppressing_lets_the_finding_speak_again():
    state = state_of(
        ("suppress_finding", payload()),
        ("unsuppress_finding", {"rule_id": "rbs-demo", "feature_id": "cds"}),
    )
    assert state.suppressions == []
    assert only(state).suppressed is False


def test_unsuppressing_something_nobody_suppressed_is_an_error():
    with pytest.raises(OperationError, match="not suppressed"):
        replay(
            SEQ, [CDS_FEATURE],
            ops(("unsuppress_finding", {"rule_id": "rbs-demo", "feature_id": "cds"})),
            is_circular=True, strict=True,
        )


def test_undo_lifts_a_suppression():
    log = [op("suppress_finding", index=0, **payload())]
    assert replay(SEQ, [CDS_FEATURE], log).suppressions
    log[0].reverted = True
    assert replay(SEQ, [CDS_FEATURE], log).suppressions == []


def test_removing_the_feature_takes_its_suppression_with_it():
    state = state_of(
        ("suppress_finding", payload()),
        ("remove_feature", {"feature_id": "cds"}),
    )
    assert state.suppressions == []
    assert lint([RULE], state) == []


# ---------------------------------------------------------------------------
# the fingerprint: what moving bases does, and what changing them does
# ---------------------------------------------------------------------------

def test_an_insertion_upstream_moves_the_window_and_keeps_the_suppression():
    """The case the whole design turns on.

    Six bases go in a long way upstream. Every coordinate downstream shifts,
    the window included — and not one base the rule read has changed, so the
    decision still stands. A suppression that died here would have people
    re-suppressing after every edit, and a linter people re-suppress is a
    linter people switch off.
    """
    state = state_of(
        ("suppress_finding", payload()),
        ("insert", {"pos": 5, "seq": "TTTTTT"}),
    )
    window = state.suppressions[0].window
    assert (window.start, window.end) == (26, 38)  # carried, not stale
    finding = only(state)
    assert (finding.window.start, finding.window.end) == (26, 38)
    assert finding.suppressed is True
    assert finding.suppression.stale is False


def test_a_deletion_upstream_moves_the_window_and_keeps_the_suppression():
    state = state_of(
        ("suppress_finding", payload()),
        ("delete", {"start": 2, "end": 10}),
    )
    assert (state.suppressions[0].window.start, state.suppressions[0].window.end) == (
        12,
        24,
    )
    assert only(state).suppressed is True


def test_editing_inside_the_window_brings_the_finding_back_marked():
    state = state_of(
        ("suppress_finding", payload()),
        ("replace", {"start": 24, "end": 27, "seq": "GGG"}),
    )
    finding = only(state)
    assert finding.suppressed is False  # back on, and blocking again
    assert finding.blocking is True
    assert finding.suppression.stale is True
    assert finding.suppression.changed == "evidence"
    assert finding.suppression.was == "CCCCCCCCCCCC"
    assert finding.suppression.now == "CCCCGGGCCCCC"
    assert finding.suppression.reason == REASON  # the decision is still shown


def test_deleting_the_window_leaves_no_coordinates_and_goes_stale():
    state = state_of(
        ("suppress_finding", payload()),
        ("delete", {"start": 20, "end": 32}),
    )
    window = state.suppressions[0].window
    assert (window.start, window.end) == (None, None)
    finding = only(state)
    assert finding.suppression.stale is True
    assert finding.suppression.now == "A" * 12


def test_flipping_the_whole_cassette_keeps_the_suppression():
    """Reading direction, not genomic order, is what the digest is over.

    A ``revcomp_region`` over the feature and its window turns every base into
    its complement and reverses the order — and the rule still reads exactly
    the same text, because the feature it reads from flipped too.
    """
    state = state_of(
        ("suppress_finding", payload()),
        ("revcomp_region", {"start": 20, "end": 47}),
    )
    cds = next(f for f in state.features if f.id == "cds")
    assert (cds.start, cds.end, cds.strand) == (20, 35, -1)
    window = state.suppressions[0].window
    assert (window.start, window.end) == (35, 47)
    assert only(state).suppressed is True


def test_moving_the_origin_keeps_the_suppression():
    state = state_of(
        ("suppress_finding", payload()),
        ("set_origin", {"pos": 40}),
    )
    window = state.suppressions[0].window
    assert (window.start, window.end) == (40, 52)
    assert only(state).suppressed is True


def test_a_rule_that_changed_its_question_invalidates_the_suppression():
    state = state_of(("suppress_finding", payload()))
    finding = only(state, WIDER)
    assert finding.suppressed is False
    assert finding.suppression.stale is True
    assert finding.suppression.changed == "rule"


def test_rewording_a_rule_does_not_invalidate_anything():
    reworded = Rule.model_validate(
        {**RULE.model_dump(), "message": "{feature}: reworded, same question"}
    )
    assert only(state_of(("suppress_finding", payload())), reworded).suppressed is True


# ---------------------------------------------------------------------------
# merging: a suppression is carried, and never conflicts
# ---------------------------------------------------------------------------

def do_merge(*, ancestor=(), target=(), branch=(), seq=SEQ,
             features=(CDS_FEATURE,), rules=(RULE,)):
    a = [op(k, index=i, **p) for i, (k, p) in enumerate(ancestor)]
    n = len(a)
    return merge_logs(
        seq,
        list(features),
        a,
        [op(k, index=n + i, **p) for i, (k, p) in enumerate(target)],
        [op(k, index=n + i, **p) for i, (k, p) in enumerate(branch)],
        is_circular=True,
        construct_id="c",
        rules=list(rules),
    )


def test_rebasing_carries_a_suppression_window_past_the_targets_insertion():
    """The merge answer to the same question the replay test asks.

    The branch suppressed a finding against bases 20..32. The target then put
    six bases in upstream, so on the merged molecule those same bases are at
    26..38. The rebase moves the window with them, the digest still matches,
    and the suppression lands intact.
    """
    result = do_merge(
        target=[("insert", {"pos": 5, "seq": "TTTTTT"})],
        branch=[("suppress_finding", payload())],
    )
    assert not result.conflicts
    assert result.rebased[0].payload["window"]["start"] == 26
    assert result.rebased[0].payload["window"]["end"] == 38
    merged = result.merged_state
    assert only(merged).suppressed is True


def test_a_suppression_never_conflicts_with_an_edit_in_its_window():
    """The asymmetry that matters: a note cannot block a merge.

    Mapping the window across an edit that landed *inside* it has no answer,
    and for an ``add_feature`` that is a conflict. Here it is not: the
    suppression moves no bases, so the merge goes through and the finding
    comes back marked stale on the other side, with the decision in front of
    whoever made it.
    """
    result = do_merge(
        target=[("replace", {"start": 24, "end": 27, "seq": "GGG"})],
        branch=[("suppress_finding", payload())],
    )
    assert not result.conflicts
    assert result.rebased[0].payload["window"]["start"] is None
    finding = only(result.merged_state)
    assert finding.suppressed is False
    assert finding.suppression.stale is True
    assert finding.suppression.was == "CCCCCCCCCCCC"


def test_suppressing_on_a_feature_the_target_removed_is_skipped():
    result = do_merge(
        target=[("remove_feature", {"feature_id": "cds"})],
        branch=[("suppress_finding", payload())],
    )
    assert not result.conflicts
    assert [c.reason for c in result.skipped] == ["already_applied"]
    assert result.merged_state.suppressions == []


def test_both_branches_suppressing_the_same_finding_converge():
    result = do_merge(
        target=[("suppress_finding", payload())],
        branch=[("suppress_finding", payload(reason="same call, other branch"))],
    )
    assert not result.conflicts
    assert len(result.merged_state.suppressions) == 1


def test_lifting_a_suppression_the_target_already_lifted_is_skipped():
    result = do_merge(
        ancestor=[("suppress_finding", payload())],
        target=[("unsuppress_finding", {"rule_id": "rbs-demo", "feature_id": "cds"})],
        branch=[("unsuppress_finding", {"rule_id": "rbs-demo", "feature_id": "cds"})],
    )
    assert not result.conflicts
    assert [c.reason for c in result.skipped] == ["already_applied"]
    assert result.merged_state.suppressions == []


def test_a_branch_can_lift_a_suppression_the_ancestor_made():
    result = do_merge(
        ancestor=[("suppress_finding", payload())],
        target=[("insert", {"pos": 5, "seq": "TTTTTT"})],
        branch=[("unsuppress_finding", {"rule_id": "rbs-demo", "feature_id": "cds"})],
    )
    assert not result.conflicts
    assert result.merged_state.suppressions == []
    assert only(result.merged_state).suppressed is False


# ---------------------------------------------------------------------------
# the gate: an error the merge introduces is a wall, and a suppression is the
# only door through it
# ---------------------------------------------------------------------------

#: A spacing rule, so a merge can break a rule without either side breaking it.
SPACING = Rule.model_validate(
    {
        **RULE.model_dump(),
        "id": "rbs-spacing",
        "region": {"where": "upstream", "window": 30},
        "expect": {"presence": "required", "distance_min": 5, "distance_max": 13},
        "message": "{feature}: Shine-Dalgarno is {distance} nt from the start codon",
        "message_missing": "{feature}: no Shine-Dalgarno in the {window} bases upstream",
    }
)

#: AGGAGG at 4..10, then a gap of 8, then the CDS. Well spaced, to start with.
SPACED_SEQ = "TTTT" + "AGGAGG" + "T" * 8 + CDS + "T" * 27
SPACED_FEATURE = Feature(id="cds", name="lacZalpha", kind="CDS", start=18, end=33)
SPACED = {"seq": SPACED_SEQ, "features": [SPACED_FEATURE], "rules": [SPACING]}


def test_neither_edit_breaks_the_spacing_rule_on_its_own():
    for side in ("target", "branch"):
        result = do_merge(**SPACED, **{side: [("insert", {"pos": 14, "seq": "TTT"})]})
        assert result.new_findings == []
        assert result.clean


def test_two_clean_edits_that_combine_into_a_rule_error_block_the_merge():
    """The rule engine's version of the premature stop codon.

    Each branch pushes the ribosome binding site three bases further from the
    start codon, and 8 -> 11 is still inside the window either way. Together
    they make it 14, which is outside it. Nobody broke anything; the merge did.
    """
    result = do_merge(
        **SPACED,
        target=[("insert", {"pos": 14, "seq": "TTT"})],
        branch=[("insert", {"pos": 12, "seq": "TTT"})],
    )
    assert not result.conflicts  # the coordinates merge perfectly well
    assert result.breaks_rules and not result.clean
    (blocked,) = result.new_findings
    assert blocked.rule_id == "rbs-spacing"
    assert "14 nt" in blocked.message


def test_an_error_both_tips_already_had_is_not_the_merges_fault():
    """The gate blames the merge, never the state it was already in.

    Blocking on any error at all would wall off every construct that already
    has one, which punishes merging rather than breaking.
    """
    result = do_merge(
        target=[("insert", {"pos": 5, "seq": "TTTTTT"})],
        branch=[("insert", {"pos": 55, "seq": "GG"})],
    )
    assert only(result.merged_state).blocking  # still an error, and still shown
    assert result.new_findings == []
    assert result.clean


ANNOTATE = (
    "add_feature",
    {
        "feature": {
            "id": "cds",
            "name": "lacZalpha",
            "kind": "CDS",
            "start": 32,
            "end": 47,
            "strand": 1,
        }
    },
)


def branch_side_payload() -> dict:
    """What the branch suppressed, computed against the branch's own state."""
    annotated = replay(SEQ, [], ops(ANNOTATE), is_circular=True)
    return suppression_payload(only(annotated), REASON)


def test_a_suppression_the_merge_leaves_standing_does_not_block_it():
    result = do_merge(
        features=[],
        target=[("insert", {"pos": 5, "seq": "TTTTTT"})],
        branch=[ANNOTATE, ("suppress_finding", branch_side_payload())],
    )
    assert result.new_findings == []
    assert result.clean
    assert only(result.merged_state).suppressed is True


def test_a_suppression_the_merge_invalidates_blocks_it():
    """The case the gate exists for.

    One branch annotated a CDS with a deliberately weak ribosome binding site
    and wrote down why. The other rewrote the bases that decision was made
    about. Neither side is broken; the merge is, because nobody has looked at
    the new sequence and said it is still deliberate.
    """
    result = do_merge(
        features=[],
        target=[("replace", {"start": 24, "end": 27, "seq": "GGG"})],
        branch=[ANNOTATE, ("suppress_finding", branch_side_payload())],
    )
    assert not result.conflicts
    assert result.breaks_rules and not result.clean

    # The finding *is* the explanation: which rule, whose decision, and both
    # readings of the window. The UI needs nothing else to say why it bounced.
    (blocked,) = result.new_findings
    assert blocked.rule_id == "rbs-demo"
    assert blocked.feature_name == "lacZalpha"
    assert blocked.suppression.reason == REASON
    assert blocked.suppression.stale is True
    assert blocked.suppression.changed == "evidence"
    assert blocked.suppression.was == "CCCCCCCCCCCC"
    assert blocked.suppression.now == "CCCCGGGCCCCC"
