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
from app.seed import seed


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
    }
    assert all(c.description for c in created)


def test_reset_clears_what_was_there_before(db):
    seed(db)
    seed(db, reset=True)
    assert len(db.scalars(select(Construct)).all()) == 4


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


def test_the_plain_puc19_is_untouched(db):
    seed(db)
    puc19 = by_name(db)["pUC19"]
    state = state_of(puc19)
    assert state.length == 2686
    assert len(state.features) == 18
    assert puc19.operations == []


def test_the_mcs_branch_is_a_branch_that_diverged(db):
    seed(db)
    everything = by_name(db)
    branch = everything["pUC19 · MCS swap"]
    assert branch.parent_id == everything["pUC19"].id
    assert branch.fork_index == 0
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
