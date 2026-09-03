"""The seeded scenarios have to actually demonstrate what they claim."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Construct
from app.domain.analysis import check_reading_frames
from app.domain.merge import merge_logs
from app.domain.models import Feature, Operation
from app.domain.replay import replay
from app.domain.rules import lint, load_rules
from app.seed import SCENARIOS, run_sheet, seed


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session


def ops_of(construct: Construct) -> list[Operation]:
    return [
        Operation(
            id=r.id,
            construct_id=r.construct_id,
            index=r.index,
            kind=r.kind,
            payload=r.payload or {},
        )
        for r in construct.operations
    ]


def state_of(construct: Construct):
    return replay(
        construct.base_sequence,
        [Feature.model_validate(f) for f in construct.base_features],
        ops_of(construct),
        is_circular=construct.is_circular,
    )


def by_name(db) -> dict[str, Construct]:
    return {c.name: c for c in db.scalars(select(Construct)).all()}


def test_seeding_creates_the_scenarios(db):
    created = seed(db)
    names = {c.name for c in created}
    assert names == {
        "pUC19",
        "pUC19 · MCS swap",
        "pUC19 · AmpR +Phe",
        "pUC19 · AmpR +Cys",
        "pUC19 · RBS spacer (lab A)",
        "pUC19 · RBS spacer (lab B)",
    }
    assert all(c.description for c in created)


def test_reset_clears_what_was_there_before(db):
    seed(db)
    seed(db, reset=True)
    assert len(db.scalars(select(Construct)).all()) == 6


def test_seeding_twice_does_not_duplicate_anything(db):
    seed(db)
    again = seed(db)
    assert again == []
    assert len(db.scalars(select(Construct)).all()) == 6


def test_seeding_restores_only_what_is_missing(db):
    seed(db)
    everything = by_name(db)
    db.delete(everything["pUC19 · MCS swap"])
    db.commit()

    restored = seed(db)
    assert [c.name for c in restored] == ["pUC19 · MCS swap"]
    assert len(db.scalars(select(Construct)).all()) == 6


def test_every_seeded_operation_actually_applies(db):
    """Strict replay raises on any stored operation that no longer fits.

    Not the same as "no warnings": the MCS swap truncates features on purpose
    and says so, which is the engine working, not failing.
    """
    for construct in seed(db):
        replay(
            construct.base_sequence,
            [Feature.model_validate(f) for f in construct.base_features],
            ops_of(construct),
            is_circular=construct.is_circular,
            strict=True,
        )
        assert not any(
            "omitida" in w for w in state_of(construct).warnings
        ), construct.name


def test_the_plain_puc19_is_the_vector_plus_two_decisions(db):
    """The log is not empty, and neither entry is an edit.

    Both of pUC19's genes are missing the strong AGGAGG consensus, which the
    rule is right about and which does not stop either of them being
    transcribed. The judgement that this molecule is fine anyway is recorded
    rather than hardcoded away, so the demo opens on "2 suppressed" instead of
    on two red errors that make the linter look broken.
    """
    seed(db)
    puc19 = by_name(db)["pUC19"]
    state = state_of(puc19)
    assert state.length == 2686
    assert len(state.features) == 18
    assert [r.kind for r in puc19.operations] == [
        "suppress_finding",
        "suppress_finding",
    ]

    findings = lint(load_rules(), state)
    quiet = [f for f in findings if f.suppressed]
    assert {f.feature_name for f in quiet} == {"lacZalpha", "bla"}
    assert all(f.suppression.reason.startswith("El consenso AGGAGG") for f in quiet)
    assert all(not f.suppression.stale for f in quiet)
    # Nothing is blocking, and nothing was hidden: the terminator warnings are
    # still on screen, because only one rule is pre-suppressed.
    assert [f.rule_id for f in findings if not f.suppressed] == [
        "cds-without-terminator",
        "cds-without-terminator",
    ]


def test_the_wild_type_decisions_cannot_drift_from_the_pack(db):
    """Computed from the engine, never written by hand.

    A suppression carries the digest of the window its rule read, so it can
    only be built by asking the engine what it just looked at. A seed that
    hardcoded one would go stale the moment anybody touched the rule.
    """
    seed(db)
    pack = load_rules()
    for row in by_name(db)["pUC19"].operations:
        assert row.payload["pack_digest"] == pack.digest
        assert row.payload["rule_id"] == "rbs-atg-spacing"


def test_every_scenario_inherits_the_wild_type_decisions(db):
    seed(db)
    for construct in by_name(db).values():
        first_two = [r.kind for r in construct.operations][:2]
        assert first_two == ["suppress_finding", "suppress_finding"], construct.name


def test_the_mcs_branch_is_a_branch_that_diverged(db):
    seed(db)
    everything = by_name(db)
    branch = everything["pUC19 · MCS swap"]
    assert branch.parent_id == everything["pUC19"].id
    assert branch.fork_index == 2  # the two wild-type decisions, and nothing else
    assert state_of(branch).length == 2686 - 50 + 18
    assert any(f.name == "new MCS" for f in state_of(branch).features)


# --------------------------------------------------------------------------
# the scenario the seed exists for
# --------------------------------------------------------------------------

def test_each_ampr_branch_is_clean_on_its_own(db):
    seed(db)
    everything = by_name(db)
    for name in ("pUC19 · AmpR +Phe", "pUC19 · AmpR +Cys"):
        state = state_of(everything[name])
        assert check_reading_frames(state) == [], name
        assert state.length == 2689


def test_merging_the_two_ampr_branches_kills_the_protein(db):
    seed(db)
    everything = by_name(db)
    target, branch = everything["pUC19 · AmpR +Phe"], everything["pUC19 · AmpR +Cys"]
    fork = branch.fork_index

    result = merge_logs(
        target.base_sequence,
        [Feature.model_validate(f) for f in target.base_features],
        ops_of(target)[:fork],
        ops_of(target)[fork:],
        ops_of(branch)[fork:],
        construct_id=target.id,
    )
    # The coordinates merge without complaint ...
    assert result.conflicts == []
    # ... and the beta-lactamase is gone anyway.
    assert result.breaks_biology
    (issue,) = result.new_frame_issues
    assert issue.feature_name == "bla"
    assert issue.problem == "premature_stop"
    assert issue.codon == 163


def test_each_rbs_branch_sits_inside_the_window_on_its_own(db):
    seed(db)
    everything = by_name(db)
    pack = load_rules()
    for name in ("pUC19 · RBS spacer (lab A)", "pUC19 · RBS spacer (lab B)"):
        blocking = [
            f
            for f in lint(pack, state_of(everything[name]))
            if f.blocking and f.feature_name == "lacZalpha"
        ]
        assert blocking == [], f"{name}: {[f.message for f in blocking]}"


def test_merging_the_two_rbs_branches_trips_the_design_rule(db):
    """The second refusal the demo shows, and it must keep being a refusal."""
    seed(db)
    everything = by_name(db)
    target = everything["pUC19 · RBS spacer (lab A)"]
    branch = everything["pUC19 · RBS spacer (lab B)"]
    fork = branch.fork_index

    result = merge_logs(
        target.base_sequence,
        [Feature.model_validate(f) for f in target.base_features],
        ops_of(target)[:fork],
        ops_of(target)[fork:],
        ops_of(branch)[fork:],
        construct_id=target.id,
        rules=load_rules(),
    )
    assert result.conflicts == []
    assert not result.breaks_biology  # the protein is untouched
    assert result.breaks_rules
    (finding,) = result.new_findings
    assert (finding.rule_id, finding.feature_name) == ("rbs-atg-spacing", "lacZalpha")
    assert "14 nt" in finding.message
    # The standing decision about the wild type does not cover this: the labs
    # replaced the very bases it was made about, so it comes back marked.
    assert finding.suppression.stale
    assert finding.suppression.changed == "evidence"
    assert finding.suppression.was != finding.suppression.now
    # bla has been missing a strong Shine-Dalgarno all along, on both tips.
    # The merge is not to blame for it and does not get charged with it.
    assert all(f.feature_name != "bla" for f in result.new_findings)


def test_the_run_sheet_links_every_step_of_the_demo(db):
    seed(db)
    sheet = "\n".join(run_sheet(db, "http://localhost:3000/"))
    steps = [s for s in SCENARIOS if s.demo]
    assert len(steps) == 3
    for scenario in steps:
        construct = by_name(db)[scenario.name]
        assert f"http://localhost:3000/constructs/{construct.id}" in sheet
    # No trailing slash doubling, whatever the caller passes.
    assert "//constructs" not in sheet
