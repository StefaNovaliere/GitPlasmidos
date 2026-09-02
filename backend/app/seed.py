"""Populate a database with the scenarios worth looking at.

Run it once after setting the backend up::

    uv run python -m app.seed

Everything here goes through the same domain and database code the API uses —
there is no back door that writes derived state directly, because the whole
point of the project is that derived state is never written.
"""

from __future__ import annotations

import argparse
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Construct, OperationRow
from app.db.session import SessionLocal, engine, init_db
from app.domain.models import Feature, Operation
from app.domain.replay import replay
from app.domain.seqio import parse_sequence_file

DATA = Path(__file__).resolve().parents[1] / "data"
PUC19 = DATA / "puc19_annotated.gb"

#: Two edits to the AmpR gene, each adding one amino acid at the same site.
#: Separately they give a full-length beta-lactamase; merged they read as a
#: stop codon and the protein dies at residue 163. Found by search, not by
#: hand - see the README.
AMPR_SITE = 2001
AMPR_EDIT_A = "AAT"  # adds a phenylalanine
AMPR_EDIT_B = "CAG"  # adds a cysteine


@dataclass
class Scenario:
    name: str
    description: str
    operations: list[tuple[str, dict]] = field(default_factory=list)
    branches: list[Scenario] = field(default_factory=list)
    #: How many of the parent's operations this branch inherits. ``None``
    #: means all of them, i.e. forked from the parent as it stands. The AmpR
    #: pair needs 0: both edits are made against untouched pUC19, which is
    #: what makes them concurrent rather than sequential.
    fork_at: int | None = None


SCENARIOS = [
    Scenario(
        name="pUC19",
        description=(
            "The reference cloning vector, 2,686 bp. Start here: the circular "
            "map, the feature list, and the single cutters in the enzyme "
            "panel are all derived from the operation log, which is empty."
        ),
        branches=[
            Scenario(
                name="pUC19 · MCS swap",
                description=(
                    "The polylinker replaced with a shorter one, plus a new "
                    "annotation. Open Branches → Compare on pUC19 to see the "
                    "diff: one replaced block, and every downstream feature "
                    "reported as shifted rather than changed."
                ),
                fork_at=0,
                operations=[
                    ("delete", {"start": 400, "end": 450}),
                    ("insert", {"pos": 400, "seq": "GAATTCGGATCCAAGCTT"}),
                    (
                        "add_feature",
                        {
                            "feature": {
                                "id": "seed-mcs",
                                "name": "new MCS",
                                "kind": "misc_feature",
                                "start": 400,
                                "end": 418,
                                "strand": 1,
                                "color": "#4caf7d",
                            }
                        },
                    ),
                ],
            ),
        ],
    ),
    Scenario(
        name="pUC19 · AmpR +Phe",
        description=(
            "One team adds a phenylalanine codon to the beta-lactamase gene. "
            "The protein is full length and the construct is clean. Open "
            "Branches → Merge to pull in the other team's equally clean "
            "edit — the merge is refused, because together they read as a "
            "stop codon and AmpR dies at residue 163."
        ),
        operations=[("insert", {"pos": AMPR_SITE, "seq": AMPR_EDIT_A})],
        branches=[
            Scenario(
                name="pUC19 · AmpR +Cys",
                description=(
                    "The other team's edit: a cysteine codon at the same site, "
                    "also giving a full-length beta-lactamase. Neither branch "
                    "has a reading-frame problem on its own."
                ),
                # Forked from untouched pUC19, not from +Phe: the two edits are
                # concurrent, which is the entire point of the scenario.
                fork_at=0,
                operations=[("insert", {"pos": AMPR_SITE, "seq": AMPR_EDIT_B})],
            ),
        ],
    ),
]


def _new_id() -> str:
    return str(uuid.uuid4())


def _domain_ops(construct_id: str, specs: list[tuple[str, dict]]) -> list[Operation]:
    return [
        Operation(
            id=_new_id(),
            construct_id=construct_id,
            index=i,
            kind=kind,
            payload=payload,
        )
        for i, (kind, payload) in enumerate(specs)
    ]


def _persist(
    db: Session,
    *,
    name: str,
    description: str,
    sequence: str,
    features: list[Feature],
    operations: list[tuple[str, dict]],
    parent: Construct | None = None,
    fork_index: int | None = None,
) -> Construct:
    """Create a construct and its log, validating every operation on the way.

    A seed that silently produced a broken construct would be worse than no
    seed at all, so each operation is replayed strictly before it is stored.
    """
    construct = Construct(
        id=_new_id(),
        name=name,
        description=description,
        is_circular=True,
        base_sequence=sequence,
        base_features=[f.model_dump() for f in features],
        parent_id=parent.id if parent else None,
        fork_index=fork_index,
    )
    db.add(construct)
    db.flush()

    ops = _domain_ops(construct.id, operations)
    replay(sequence, features, ops, is_circular=True, strict=True)
    for op in ops:
        db.add(
            OperationRow(
                id=op.id,
                construct_id=construct.id,
                index=op.index,
                kind=op.kind,
                payload=op.payload,
                reverted=False,
            )
        )
    return construct


def seed(db: Session, *, reset: bool = False) -> list[Construct]:
    """Create the demo constructs, skipping any that are already there.

    Idempotent on purpose: running it twice should leave you with the same
    four scenarios, not eight. ``reset`` deletes everything first, which is
    what you want after playing with the demo.
    """
    if reset:
        for construct in db.scalars(select(Construct)).all():
            db.delete(construct)
        db.flush()

    record = parse_sequence_file(PUC19.read_text(), PUC19.name)
    existing = {c.name: c for c in db.scalars(select(Construct)).all()}
    created: list[Construct] = []

    for scenario in SCENARIOS:
        # Reuse the parent when it is already there, so a branch deleted on
        # its own is restored rather than skipped along with its parent.
        parent = existing.get(scenario.name)
        if parent is None:
            parent = _persist(
                db,
                name=scenario.name,
                description=scenario.description,
                sequence=record.sequence,
                features=record.features,
                operations=scenario.operations,
            )
            created.append(parent)
        for child in scenario.branches:
            if child.name in existing:
                continue
            fork = (
                len(scenario.operations) if child.fork_at is None else child.fork_at
            )
            created.append(
                _persist(
                    db,
                    name=child.name,
                    description=child.description,
                    sequence=record.sequence,
                    features=record.features,
                    # A branch carries the shared history, then its own edits.
                    operations=scenario.operations[:fork] + child.operations,
                    parent=parent,
                    fork_index=fork,
                )
            )
    db.commit()
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete every existing construct first",
    )
    args = parser.parse_args()

    init_db()
    with SessionLocal() as db:
        created = seed(db, reset=args.reset)
        if not created:
            print("Everything is already seeded. Use --reset to start over.")
            return
        for construct in created:
            state = replay(
                construct.base_sequence,
                [Feature.model_validate(f) for f in construct.base_features],
                [
                    Operation(
                        id=row.id,
                        construct_id=row.construct_id,
                        index=row.index,
                        kind=row.kind,
                        payload=row.payload or {},
                    )
                    for row in construct.operations
                ],
                is_circular=True,
            )
            mark = "  └─ " if construct.parent_id else ""
            edits = len(construct.operations)
            print(
                f"{mark}{construct.name}  {state.length:,} bp, "
                f"{edits} edit{'' if edits == 1 else 's'}  {construct.id}"
            )
    print(f"\nSeeded {len(created)} constructs into {engine.url}.")


if __name__ == "__main__":
    main()
