"""Reading-frame integrity, with the origin-crossing cases done properly.

The fixtures are built by *construction* rather than by hand-written
coordinates: a CDS with a known protein is placed in a plasmid, and
``set_origin`` rotates the molecule until the CDS straddles position 0. That
way the wraparound coordinates are derived by the code under test instead of
being asserted from arithmetic done in my head, and the expected protein stays
obvious.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.analysis import check_reading_frames, coding_sequence
from app.domain.circular import revcomp, slice_span
from app.domain.models import ConstructState
from app.domain.replay import replay
from app.domain.seqio import parse_sequence_file
from tests.conftest import feat, ops

DATA = Path(__file__).parent / "data"

#  ATG + 8 sense codons + TAA  ->  30 bp, protein "MKKKKKKKK*"
CDS = "ATG" + "AAA" * 8 + "TAA"
PROTEIN = "M" + "K" * 8
#  30 bp of padding with no ATG and no in-frame stop.
FILLER = "GGGCCC" * 5
PLASMID = FILLER + CDS  # 60 bp, CDS at [30, 60)

#  Rotating by this much lands the CDS's stop codon across position 0:
#  its three bases end up at 59, 0 and 1.
ORIGIN_SPLITTING_THE_STOP = 58


def build(sequence: str, start: int, end: int, *, strand: int = 1, edits=()):
    """A derived state holding one CDS, after the given operations."""
    base = [feat(start, end, id="cds", name="gfp", kind="CDS", strand=strand)]
    return replay(sequence, base, ops(*edits))


def cds_of(state: ConstructState):
    return next(f for f in state.features if f.id == "cds")


def problems(state: ConstructState) -> list[str]:
    return [i.problem for i in check_reading_frames(state)]


# --------------------------------------------------------------------------
# baseline: the linear case has to be right before wraparound means anything
# --------------------------------------------------------------------------

def test_a_well_formed_cds_reports_nothing():
    state = build(PLASMID, 30, 60)
    assert check_reading_frames(state) == []
    assert coding_sequence(state, cds_of(state)) == CDS


def test_a_length_that_is_not_a_multiple_of_three_is_a_frameshift():
    state = build(PLASMID, 30, 60, edits=[("insert", {"pos": 33, "seq": "T"})])
    (issue,) = check_reading_frames(state)
    assert issue.problem == "frameshift"
    assert issue.severity == "error"
    assert issue.blocking is True
    assert "31 bp" in issue.detail


def test_an_in_frame_internal_stop_is_a_premature_stop():
    state = build(PLASMID, 30, 60, edits=[("insert", {"pos": 33, "seq": "TAA"})])
    (issue,) = check_reading_frames(state)
    assert issue.problem == "premature_stop"
    assert issue.codon == 2
    assert issue.blocking is True


def test_a_frameshift_suppresses_the_downstream_translation_noise():
    # One report, not a frameshift plus every stop it happens to create.
    state = build(PLASMID, 30, 60, edits=[("insert", {"pos": 33, "seq": "TA"})])
    assert problems(state) == ["frameshift"]


def test_non_coding_features_are_ignored():
    base = [feat(30, 59, id="p", name="Plac", kind="promoter")]
    assert check_reading_frames(replay(PLASMID, base, [])) == []


def test_a_minus_strand_cds_is_reverse_complemented_before_translating():
    sequence = FILLER + revcomp(CDS)
    state = build(sequence, 30, 60, strand=-1)
    assert coding_sequence(state, cds_of(state)) == CDS
    assert check_reading_frames(state) == []


def test_a_minus_strand_cds_read_on_the_wrong_strand_would_be_caught():
    # Same bases, annotated on the plus strand: the guard has to notice.
    sequence = FILLER + revcomp(CDS)
    assert problems(build(sequence, 30, 60, strand=1)) != []


# --------------------------------------------------------------------------
# the origin-crossing cases — the reason this file exists
# --------------------------------------------------------------------------

@pytest.fixture
def wrapped():
    """The same CDS, rotated until its stop codon straddles position 0."""
    return build(
        PLASMID, 30, 60,
        edits=[("set_origin", {"pos": ORIGIN_SPLITTING_THE_STOP})],
    )


def test_the_fixture_really_does_straddle_the_origin(wrapped):
    cds = cds_of(wrapped)
    assert (cds.start, cds.end) == (32, 2)  # start > end: it wraps
    assert wrapped.length == 60
    # The stop codon's three bases sit at 59, 0 and 1.
    assert wrapped.sequence[59] + wrapped.sequence[0:2] == "TAA"


def test_neither_segment_is_a_multiple_of_three(wrapped):
    """Why a per-segment implementation is wrong, stated as an assertion.

    Translating ``seq[32:60]`` and ``seq[0:2]`` separately would report a
    frameshift on both halves and lose the codon that spans the origin.
    """
    cds = cds_of(wrapped)
    tail = wrapped.length - cds.start
    head = cds.end
    assert tail % 3 != 0 and head % 3 != 0
    assert (tail + head) % 3 == 0


def test_an_origin_crossing_cds_is_assembled_as_one_continuous_sequence(wrapped):
    cds = cds_of(wrapped)
    assert slice_span(wrapped.sequence, cds.start, cds.end, True) == CDS
    assert coding_sequence(wrapped, cds) == CDS


def test_an_origin_crossing_cds_is_not_a_false_positive(wrapped):
    assert check_reading_frames(wrapped) == []


def test_the_stop_codon_spanning_the_origin_counts_as_the_terminal_stop(wrapped):
    from Bio.Seq import Seq

    protein = str(Seq(coding_sequence(wrapped, cds_of(wrapped))).translate())
    assert protein == PROTEIN + "*"
    assert problems(wrapped) == []  # not "premature_stop", not "no_stop_codon"


@pytest.mark.parametrize("origin", range(1, 60))
def test_rotating_the_plasmid_never_changes_the_verdict(origin):
    """The strongest statement available: frame validity is origin-independent.

    Sweeping every origin covers the CDS wholly inside the sequence, wrapping
    with the split at each of the three codon offsets, and starting exactly at
    position 0 — without enumerating those cases by hand.
    """
    state = build(PLASMID, 30, 60, edits=[("set_origin", {"pos": origin})])
    assert coding_sequence(state, cds_of(state)) == CDS
    assert check_reading_frames(state) == []


@pytest.mark.parametrize("origin", range(1, 60))
def test_a_frameshift_is_detected_from_every_origin(origin):
    """And so is its absence: a broken CDS stays broken however you rotate it."""
    state = build(
        PLASMID, 30, 60,
        edits=[("insert", {"pos": 33, "seq": "T"}), ("set_origin", {"pos": origin})],
    )
    assert problems(state) == ["frameshift"]


# --------------------------------------------------------------------------
# editing an origin-crossing CDS
# --------------------------------------------------------------------------

def test_an_in_frame_stop_inserted_into_a_wrapped_cds_is_a_premature_stop():
    """Gemini's exact question: a stop introduced into an origin-crossing CDS.

    Inserting a whole codon keeps the length a multiple of 3, so this isolates
    the premature-stop check from the frameshift check — and the CDS still
    wraps afterwards, so the stop is only visible if the span was assembled
    across the origin first.
    """
    state = build(
        PLASMID, 30, 60,
        edits=[
            ("set_origin", {"pos": ORIGIN_SPLITTING_THE_STOP}),
            ("insert", {"pos": 35, "seq": "TAA"}),  # start of codon 2
        ],
    )
    cds = cds_of(state)
    assert (cds.start, cds.end) == (32, 2)  # still wrapping
    assert state.length == 63
    assert coding_sequence(state, cds) == "ATG" + "TAA" + "AAA" * 8 + "TAA"

    (issue,) = check_reading_frames(state)
    assert issue.problem == "premature_stop"
    assert issue.codon == 2
    assert "truncating the protein to 1 aa" in issue.detail


def test_an_out_of_frame_insert_into_a_wrapped_cds_is_a_frameshift():
    state = build(
        PLASMID, 30, 60,
        edits=[
            ("set_origin", {"pos": ORIGIN_SPLITTING_THE_STOP}),
            ("insert", {"pos": 35, "seq": "TT"}),
        ],
    )
    (issue,) = check_reading_frames(state)
    assert issue.problem == "frameshift"
    assert "32 bp" in issue.detail


def test_an_insert_at_the_origin_inside_a_wrapped_cds_shifts_its_frame():
    """Position 0 is *inside* a wrapping CDS, which is easy to get wrong."""
    state = build(
        PLASMID, 30, 60,
        edits=[
            ("set_origin", {"pos": ORIGIN_SPLITTING_THE_STOP}),
            ("insert", {"pos": 0, "seq": "C"}),
        ],
    )
    assert problems(state) == ["frameshift"]


def test_an_insert_outside_a_wrapped_cds_leaves_its_frame_alone():
    state = build(
        PLASMID, 30, 60,
        edits=[
            ("set_origin", {"pos": ORIGIN_SPLITTING_THE_STOP}),
            ("insert", {"pos": 20, "seq": "C"}),  # in the gap, not the CDS
        ],
    )
    assert check_reading_frames(state) == []
    assert coding_sequence(state, cds_of(state)) == CDS


def test_an_in_frame_deletion_inside_a_wrapped_cds_keeps_the_frame():
    state = build(
        PLASMID, 30, 60,
        edits=[
            ("set_origin", {"pos": ORIGIN_SPLITTING_THE_STOP}),
            ("delete", {"start": 35, "end": 38}),  # one whole codon
        ],
    )
    assert problems(state) == []
    assert coding_sequence(state, cds_of(state)) == "ATG" + "AAA" * 7 + "TAA"


def test_undoing_the_break_clears_the_issue():
    """Frame issues are derived, so undo removes them the same way it removes
    a truncation flag."""
    edits = ops(
        ("set_origin", {"pos": ORIGIN_SPLITTING_THE_STOP}),
        ("insert", {"pos": 35, "seq": "TT"}),
    )
    base = [feat(30, 60, id="cds", name="gfp", kind="CDS")]
    assert problems(replay(PLASMID, base, edits)) == ["frameshift"]
    edits[1].reverted = True
    assert check_reading_frames(replay(PLASMID, base, edits)) == []


# --------------------------------------------------------------------------
# no false positives on a real plasmid
# --------------------------------------------------------------------------

def test_real_puc19_reports_no_frame_issues():
    record = parse_sequence_file(
        (DATA / "puc19_annotated.gb").read_text(), "puc19.gb"
    )
    state = ConstructState(
        sequence=record.sequence, features=record.features, is_circular=True
    )
    assert [f.name for f in state.features if f.kind == "CDS"] == [
        "lacZalpha",
        "bla",
    ]
    assert check_reading_frames(state) == []


def test_breaking_bla_in_puc19_is_reported():
    record = parse_sequence_file(
        (DATA / "puc19_annotated.gb").read_text(), "puc19.gb"
    )
    # bla is 1626..2486 on the minus strand; drop one base inside it.
    state = replay(
        record.sequence,
        record.features,
        ops(("delete", {"start": 2000, "end": 2001})),
    )
    reported = {i.feature_name: i.problem for i in check_reading_frames(state)}
    assert reported == {"bla": "frameshift"}
