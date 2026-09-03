"""Design rules: the schema, the loader, and the engine that runs them."""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from app.domain.models import ConstructState
from app.domain.rules.cli import check_examples
from app.domain.rules.engine import (
    evaluate,
    find_motif,
    gap_to_feature,
    lint,
    motif_pattern,
    region_span,
)
from app.domain.rules.loader import DEFAULT_RULES_DIR, example_state, load_rules
from app.domain.rules.models import Rule
from tests.conftest import feat

MINIMAL = {
    "id": "demo-rule",
    "title": "A demonstration rule",
    "severity": "warning",
    "target": {"feature_kind": "CDS"},
    "region": {"where": "upstream", "window": 30},
    "look": {"motif": "AGGAGG"},
    "expect": {"presence": "required"},
    "message": "{feature}: nothing found",
    "evidence": {
        "citation": "doi:10.0000/example",
        "organism": "Escherichia coli",
        "confidence": "reported",
    },
    "examples": [{"name": "x", "sequence": "ACGT", "triggers": False}],
}


def rule(**overrides) -> Rule:
    return Rule.model_validate({**MINIMAL, **overrides})


def state(sequence: str, features=(), *, is_circular: bool = False):
    return ConstructState(
        sequence=sequence, features=list(features), is_circular=is_circular
    )


# --------------------------------------------------------------------------
# the schema is the review process
# --------------------------------------------------------------------------

def test_a_rule_without_a_citation_is_refused():
    with pytest.raises(ValidationError):
        rule(evidence={"citation": "", "organism": "E. coli",
                       "confidence": "reported"})


def test_a_rule_without_an_organism_is_refused():
    """A threshold measured in one host does not transfer to another."""
    with pytest.raises(ValidationError):
        rule(evidence={"citation": "doi:10.0000/x", "organism": "",
                       "confidence": "reported"})


def test_a_rule_without_examples_is_refused():
    with pytest.raises(ValidationError):
        rule(examples=[])


def test_a_heuristic_cannot_be_an_error():
    """A linter that blocks merges on unmeasured numbers gets switched off."""
    with pytest.raises(ValidationError, match="heuristic"):
        rule(
            severity="error",
            evidence={"citation": "doi:10.0000/x", "organism": "E. coli",
                      "confidence": "heuristic"},
        )
    # The same rule as a warning is fine.
    assert rule(
        severity="warning",
        evidence={"citation": "doi:10.0000/x", "organism": "E. coli",
                  "confidence": "heuristic"},
    ).severity == "warning"


def test_a_motif_outside_iupac_is_refused():
    with pytest.raises(ValidationError, match="IUPAC"):
        rule(look={"motif": "AGGAGZ"})


def test_a_look_must_choose_a_motif_or_a_feature():
    with pytest.raises(ValidationError, match="exactly one"):
        rule(look={"motif": "AGG", "feature_kind": "promoter"})
    with pytest.raises(ValidationError, match="exactly one"):
        rule(look={})


def test_a_forbidden_hit_cannot_carry_a_distance():
    with pytest.raises(ValidationError):
        rule(expect={"presence": "forbidden", "distance_max": 10})


def test_unknown_fields_are_refused_rather_than_ignored():
    """A typo in a rule file must be loud, not silently dropped."""
    with pytest.raises(ValidationError):
        rule(regoin={"where": "upstream", "window": 30})


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def test_a_broken_file_is_reported_not_raised(tmp_path):
    (tmp_path / "good.yaml").write_text(yaml.safe_dump(MINIMAL))
    (tmp_path / "bad.yaml").write_text("id: no-severity\ntitle: Missing things\n")
    (tmp_path / "notyaml.yaml").write_text("{{{")

    loaded = load_rules(tmp_path)
    assert [r.id for r in loaded.rules] == ["demo-rule"]
    assert len(loaded.errors) == 2
    assert any("bad.yaml" in e for e in loaded.errors)


def test_two_rules_cannot_share_an_id(tmp_path):
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(MINIMAL))
    (tmp_path / "b.yaml").write_text(yaml.safe_dump(MINIMAL))
    loaded = load_rules(tmp_path)
    assert len(loaded.rules) == 1
    assert any("already used" in e for e in loaded.errors)


def test_a_missing_directory_is_an_error_not_a_crash(tmp_path):
    loaded = load_rules(tmp_path / "nope")
    assert loaded.rules == []
    assert loaded.errors


# --------------------------------------------------------------------------
# reading direction — the part that is easy to get backwards
# --------------------------------------------------------------------------

def test_upstream_of_a_plus_strand_gene_is_lower_coordinates():
    plus = feat(100, 200, id="a", strand=1)
    assert region_span(
        rule(region={"where": "upstream", "window": 30}).region, plus, 1000, False
    ) == (70, 100)


def test_upstream_of_a_minus_strand_gene_is_higher_coordinates():
    """Its 5' end is at `end`, so upstream runs the other way."""
    minus = feat(100, 200, id="a", strand=-1)
    assert region_span(
        rule(region={"where": "upstream", "window": 30}).region, minus, 1000, False
    ) == (200, 230)


def test_downstream_mirrors_upstream_on_each_strand():
    region = rule(region={"where": "downstream", "window": 30}).region
    assert region_span(region, feat(100, 200, id="a", strand=1), 1000, False) == (200, 230)
    assert region_span(region, feat(100, 200, id="b", strand=-1), 1000, False) == (70, 100)


def test_a_window_off_the_end_wraps_on_a_circular_construct():
    region = rule(region={"where": "upstream", "window": 30}).region
    assert region_span(region, feat(10, 50, id="a"), 1000, True) == (980, 10)


def test_the_gap_is_measured_in_the_genes_own_direction():
    plus, minus = feat(100, 200, id="a"), feat(100, 200, id="b", strand=-1)
    assert gap_to_feature((80, 90), plus, 1000, False) == 10
    assert gap_to_feature((210, 220), minus, 1000, False) == 10


# --------------------------------------------------------------------------
# motifs
# --------------------------------------------------------------------------

def test_degenerate_motifs_match_what_they_stand_for():
    """A consensus written as TTGACR must not be searched literally."""
    assert motif_pattern("TTGACR").fullmatch("TTGACA")
    assert motif_pattern("TTGACR").fullmatch("TTGACG")
    assert not motif_pattern("TTGACR").fullmatch("TTGACC")
    assert motif_pattern("NNN").fullmatch("ACG")


def test_a_motif_across_the_origin_is_found_once():
    assert find_motif("AGGACCCAGG", "AGGA", True) == [(0, 4), (7, 11)]
    assert find_motif("AGGACCCAGG", "AGGA", False) == [(0, 4)]


def test_overlapping_occurrences_are_all_found():
    assert find_motif("AAAA", "AA", False) == [(0, 2), (1, 3), (2, 4)]


# --------------------------------------------------------------------------
# the engine
# --------------------------------------------------------------------------

def test_a_required_motif_that_is_missing_is_reported():
    found = evaluate(
        rule(),
        state("CCCCCCCCCCCCATGAAA", [feat(12, 18, id="c", kind="CDS", name="gene")]),
    )
    assert len(found) == 1
    assert found[0].feature_name == "gene"
    assert found[0].rule_id == "demo-rule"


def test_a_required_motif_that_is_present_is_not_reported():
    found = evaluate(
        rule(),
        state("AGGAGGTTTTATGAAA", [feat(10, 16, id="c", kind="CDS", name="gene")]),
    )
    assert found == []


def test_a_distance_outside_the_range_is_reported_with_the_number():
    spaced = rule(
        expect={"presence": "required", "distance_min": 5, "distance_max": 13},
        message="{feature}: {distance} nt away",
        message_missing="{feature}: nothing found",
    )
    #  AGGAGG at 0-6, ATG at 26 -> a gap of 20
    sequence = "AGGAGG" + "T" * 20 + "ATGAAA"
    found = evaluate(spaced, state(sequence, [feat(26, 32, id="c", kind="CDS", name="g")]))
    assert len(found) == 1 and "20 nt away" in found[0].message


def test_finding_nothing_reads_as_finding_nothing():
    """Two failures, two diagnoses.

    A motif that is absent and a motif at the wrong spacing are different
    biological problems, and a biologist acts on them differently. One template
    cannot say both: it would have to interpolate a distance that does not
    exist.
    """
    spaced = rule(
        expect={"presence": "required", "distance_min": 5, "distance_max": 13},
        message="{feature}: Shine-Dalgarno is {distance} nt from the start codon",
        message_missing="{feature}: no Shine-Dalgarno ({motif}) in the {window} bases upstream",
    )
    found = evaluate(
        spaced, state("T" * 26 + "ATGAAA", [feat(26, 32, id="c", kind="CDS", name="g")])
    )
    assert len(found) == 1
    assert found[0].message == "g: no Shine-Dalgarno (AGGAGG) in the 30 bases upstream"


def test_a_rule_that_can_find_nothing_must_say_so_in_its_own_words():
    with pytest.raises(ValidationError, match="message_missing"):
        rule(
            expect={"presence": "required", "distance_max": 13},
            message="{feature}: {distance} nt away",
        )


def test_message_missing_cannot_interpolate_a_distance_that_does_not_exist():
    with pytest.raises(ValidationError, match="no distance"):
        rule(message_missing="{feature}: nothing at {distance} nt")


def test_a_forbidden_rule_has_no_absence_to_report():
    with pytest.raises(ValidationError, match="no absence"):
        rule(
            expect={"presence": "forbidden"},
            message="{feature}: found one",
            message_missing="{feature}: found none",
        )


def test_a_forbidden_feature_is_reported_where_it_sits():
    forbidden = rule(
        severity="warning",
        region={"where": "downstream", "window": 200},
        look={"feature_kind": "promoter", "strand": "opposite"},
        expect={"presence": "forbidden"},
        message="{feature}: promoter firing back",
    )
    found = evaluate(
        forbidden,
        state(
            "A" * 300,
            [
                feat(0, 15, id="c", kind="CDS", name="gene", strand=1),
                feat(30, 40, id="p", kind="promoter", name="P2", strand=-1),
            ],
        ),
    )
    assert len(found) == 1
    assert (found[0].start, found[0].end) == (30, 40)


def test_a_feature_on_the_same_strand_does_not_trip_an_opposite_strand_rule():
    forbidden = rule(
        severity="warning",
        region={"where": "downstream", "window": 200},
        look={"feature_kind": "promoter", "strand": "opposite"},
        expect={"presence": "forbidden"},
    )
    found = evaluate(
        forbidden,
        state(
            "A" * 300,
            [
                feat(0, 15, id="c", kind="CDS", name="gene", strand=1),
                feat(30, 40, id="p", kind="promoter", name="P2", strand=1),
            ],
        ),
    )
    assert found == []


def test_only_the_targeted_feature_kind_is_examined():
    found = evaluate(
        rule(target={"feature_kind": "CDS"}),
        state("CCCCCCCCCCCC", [feat(0, 12, id="p", kind="promoter", name="P")]),
    )
    assert found == []


def test_findings_come_back_errors_first():
    seq = "CCCCCCCCCCCCATGAAA"
    features = [feat(12, 18, id="c", kind="CDS", name="gene")]
    warn = rule(id="warn-rule", severity="warning")
    err = rule(
        id="err-rule",
        severity="error",
        evidence={**MINIMAL["evidence"], "confidence": "established"},
    )
    assert [f.severity for f in lint([warn, err], state(seq, features))] == [
        "error",
        "warning",
    ]


def test_an_empty_construct_reports_nothing():
    assert evaluate(rule(), state("", [])) == []


# --------------------------------------------------------------------------
# the shipped pack
# --------------------------------------------------------------------------

def test_the_shipped_pack_loads_without_errors():
    loaded = load_rules(DEFAULT_RULES_DIR)
    assert loaded.errors == []
    assert {r.id for r in loaded.rules} == {
        "rbs-atg-spacing",
        "cds-without-terminator",
        "convergent-promoter",
    }


def test_every_shipped_rule_passes_its_own_examples():
    """The gate that lets biology add rules without an engineer reviewing them."""
    for shipped in load_rules(DEFAULT_RULES_DIR).rules:
        assert check_examples(shipped) == [], shipped.id


def test_every_shipped_rule_cites_something_followable():
    for shipped in load_rules(DEFAULT_RULES_DIR).rules:
        assert "doi:" in shipped.evidence.citation.lower(), shipped.id
        assert shipped.evidence.organism


def test_example_shorthand_builds_the_state_it_describes():
    from app.domain.rules.models import Example

    built = example_state(
        Example(
            name="x",
            sequence="ACGTACGTAC",
            features=["CDS:2-8:-:gene"],
            triggers=False,
        )
    )
    (only,) = built.features
    assert (only.kind, only.start, only.end, only.strand, only.name) == (
        "CDS", 2, 8, -1, "gene",
    )
