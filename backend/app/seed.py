"""Populate a database with the scenarios worth looking at.

Run it once after setting the backend up::

    uv run python -m app.seed

Everything here goes through the same domain and database code the API uses —
there is no back door that writes derived state directly, because the whole
point of the project is that derived state is never written.
"""

from __future__ import annotations

import argparse
import textwrap
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Construct, OperationRow
from app.db.session import SessionLocal, engine, init_db
from app.domain.models import ConstructState, Feature, Operation
from app.domain.replay import replay
from app.domain.rules import RuleSet, lint, load_rules, suppression_payload
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

#: Wild-type pUC19 trips ``rbs-atg-spacing`` on both of its genes: neither
#: lacZ-alpha nor bla carries the strong AGGAGG consensus, and both are
#: transcribed anyway. The rule is not wrong about what it measures - AGGAGG is
#: the strong form and it is genuinely absent - so it stays an ``error``. What
#: is a judgement is that *this* molecule is fine regardless, and a judgement
#: belongs in the log where somebody can read it, undo it, or disagree with it.
#:
#: So the demo opens on "2 findings, 2 suppressed" rather than on two red
#: errors that make the linter look broken. The infrastructure exists exactly
#: because rules are imperfect; hiding that would be the wrong lesson.
WILD_TYPE_REASON = (
    "El consenso AGGAGG estricto es demasiado rígido para el wild-type "
    "pUC19. Mantener suprimido."
)
#: Only this rule is pre-suppressed. A different rule firing on the wild type
#: is news, and news deserves somebody's decision rather than a canned one.
WILD_TYPE_RULE = "rbs-atg-spacing"

#: A ribosome binding site tuned to sit 8 nt from the lacZ-alpha start codon,
#: comfortably inside the 5-13 nt window the rule pack cites. lacZ-alpha is on
#: the minus strand, so "upstream" is *higher* coordinates and the site reads
#: CCTCCT on the plus strand — AGGAGG as the gene itself reads it.
LACZ_RBS_SITE = 477
LACZ_RBS = "CCTCCT"
#: Three bases each, dropped into the spacer between the site and the ATG.
#: 8 nt becomes 11 either way, still inside the window; together it is 14.
LACZ_SPACER_A = (473, "TTT")
LACZ_SPACER_B = (471, "AAA")


@dataclass
class Scenario:
    name: str
    description: str
    #: What to do on this construct in a live demo. Constructs that carry one
    #: are printed as numbered steps with their URL, so the demo is a list of
    #: links rather than a memory test.
    demo: str = ""
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
        demo=(
            "Nothing to click yet. The circular map, the 18 features and the "
            "single cutters in the enzyme panel are all derived from the "
            "operation log — which holds exactly two entries, and neither is "
            "an edit. Both genes are missing the strong AGGAGG consensus, the "
            "rule is right about that, and somebody decided this molecule is "
            "fine anyway. The panel reads \"2 findings, 2 suppressed\" and the "
            "reason is in the history, where it can be read, undone or "
            "disagreed with."
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
        demo=(
            "Branches → Merge \"pUC19 · AmpR +Cys\". Refused: the two clean "
            "codons read across a boundary as a stop, and beta-lactamase dies "
            "at residue 163 of 289. The dialog shows all three reading frames "
            "as codons."
        ),
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
    Scenario(
        name="pUC19 · RBS spacer (lab A)",
        description=(
            "The 5' untranslated region of lacZ-alpha, with a strong ribosome "
            "binding site 8 nt from the start codon, and three bases added to "
            "the spacer. Still inside the 5-13 nt window, so the construct is "
            "clean. Open Branches → Merge to pull in the other lab's equally "
            "clean three bases."
        ),
        operations=[
            ("insert", {"pos": LACZ_RBS_SITE, "seq": LACZ_RBS}),
            ("insert", {"pos": LACZ_SPACER_A[0], "seq": LACZ_SPACER_A[1]}),
        ],
        demo=(
            "Branches → Merge \"pUC19 · RBS spacer (lab B)\". Refused by a "
            "design rule this time: 8 + 3 + 3 puts the Shine-Dalgarno 14 nt "
            "from the ATG, outside the window Shine and Dalgarno measured. "
            "Write a reason and merge anyway — it lands in the history as an "
            "operation, not as a flag."
        ),
        branches=[
            Scenario(
                name="pUC19 · RBS spacer (lab B)",
                description=(
                    "The other lab's three bases, added at a different point "
                    "in the same spacer. Also clean on its own: 11 nt is a "
                    "perfectly good distance."
                ),
                # Forked after the site was tuned, before either edit: the two
                # spacer edits are concurrent, which is the whole point.
                fork_at=1,
                operations=[
                    ("insert", {"pos": LACZ_SPACER_B[0], "seq": LACZ_SPACER_B[1]})
                ],
            ),
        ],
    ),
]


def wild_type_decisions(
    sequence: str, features: list[Feature], pack: RuleSet
) -> list[tuple[str, dict]]:
    """The two decisions every seeded construct starts from.

    Computed, never written by hand: a suppression carries the digest of the
    window its rule read, so it can only be built by asking the engine what it
    just looked at. That also means the seed cannot drift from the pack — if
    the rule changes, these are recorded against the rule as it actually is,
    and if it stops firing they simply are not created.
    """
    state = ConstructState(sequence=sequence, features=features, is_circular=True)
    return [
        ("suppress_finding", suppression_payload(finding, WILD_TYPE_REASON))
        for finding in lint(pack, state)
        if finding.blocking and finding.rule_id == WILD_TYPE_RULE
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
    # Shared prehistory: every scenario is wild-type pUC19 plus its own edits,
    # so the two standing decisions about the wild type belong to all of them.
    prelude = wild_type_decisions(record.sequence, record.features, load_rules())
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
                operations=prelude + scenario.operations,
            )
            created.append(parent)
        for child in scenario.branches:
            if child.name in existing:
                continue
            # ``fork_at`` counts the scenario's own operations; the prelude is
            # ancestry, and every branch inherits all of it.
            fork = len(prelude) + (
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
                    operations=(prelude + scenario.operations)[:fork]
                    + child.operations,
                    parent=parent,
                    fork_index=fork,
                )
            )
    db.commit()
    return created


def run_sheet(db: Session, base_url: str) -> list[str]:
    """The demo, as a list of links and one instruction each.

    A live demo typed from memory is a demo that goes wrong in front of the
    person you wanted to impress. Every scenario that is worth showing carries
    the sentence describing what to click, and this prints them in order with
    the URL already resolved.
    """
    by_name = {c.name: c for c in db.scalars(select(Construct)).all()}
    lines: list[str] = []
    step = 0
    for scenario in SCENARIOS:
        construct = by_name.get(scenario.name)
        if not scenario.demo or construct is None:
            continue
        step += 1
        lines.append(f"  {step}. {scenario.name}")
        lines.append(f"     {base_url.rstrip('/')}/constructs/{construct.id}")
        lines.extend(
            f"     {line}"
            for line in textwrap.wrap(scenario.demo, width=72)
        )
        lines.append("")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete every existing construct first",
    )
    parser.add_argument(
        "--url",
        default="http://localhost:3000",
        help="where the frontend is served, for the printed demo links",
    )
    args = parser.parse_args()

    init_db()
    with SessionLocal() as db:
        created = seed(db, reset=args.reset)
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
            # "operations", not "edits": a suppression is one of these and it
            # edits nothing.
            count = len(construct.operations)
            print(
                f"{mark}{construct.name}  {state.length:,} bp, "
                f"{count} operation{'' if count == 1 else 's'}  {construct.id}"
            )
        if created:
            print(f"\nSeeded {len(created)} constructs into {engine.url}.")
        else:
            print("Everything is already seeded. Use --reset to start over.")

        print("\nDemo, in order:\n")
        print("\n".join(run_sheet(db, args.url)))


if __name__ == "__main__":
    main()
