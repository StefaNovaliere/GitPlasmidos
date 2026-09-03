"""End-to-end API tests against an in-memory SQLite database."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_db
from app.main import app

DATA = Path(__file__).parents[1] / "data"
PUC19 = DATA / "puc19_annotated.gb"


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def import_puc19(client) -> dict:
    resp = client.post(
        "/api/constructs/import",
        files={"file": ("puc19.gb", PUC19.read_bytes(), "chemical/x-genbank")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def apply(client, cid: str, kind: str, **payload):
    return client.post(
        f"/api/constructs/{cid}/operations", json={"kind": kind, "payload": payload}
    )


# --------------------------------------------------------------------------
# create, import, list, read
# --------------------------------------------------------------------------

def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_create_empty_construct(client):
    body = client.post("/api/constructs", json={"name": "blank"}).json()
    assert body["length"] == 0
    assert body["is_circular"] is True
    assert body["can_undo"] is False and body["can_redo"] is False


def test_create_from_raw_sequence(client):
    body = client.post(
        "/api/constructs", json={"name": "raw", "sequence": "acgtacgtGG"}
    ).json()
    assert body["sequence"] == "ACGTACGTGG"
    assert body["length"] == 10
    assert body["gc_content"] == pytest.approx(0.6)


def test_create_rejects_non_iupac_characters(client):
    resp = client.post("/api/constructs", json={"name": "bad", "sequence": "ACGTX"})
    assert resp.status_code == 422
    assert "Invalid nucleotide" in resp.json()["detail"]


def test_create_rejects_out_of_bounds_features(client):
    resp = client.post(
        "/api/constructs",
        json={
            "name": "bad",
            "sequence": "ACGTACGTAC",
            "features": [{"id": "a", "name": "x", "start": 2, "end": 999}],
        },
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------
# acceptance criterion 1: import pUC19 over HTTP
# --------------------------------------------------------------------------

def test_import_puc19(client):
    body = import_puc19(client)
    assert body["length"] == 2686
    assert body["is_circular"] is True
    assert body["name"] == "pUC19"
    assert len(body["features"]) == 18
    names = {f["name"] for f in body["features"]}
    assert {"lacZalpha", "bla"} <= names
    assert any("unparseable" in w for w in body["import_warnings"])


def test_import_fasta_defaults_to_circular_with_a_warning(client):
    resp = client.post(
        "/api/constructs/import",
        files={"file": ("x.fasta", b">demo\nACGTACGTACGTACGT\n", "text/plain")},
    )
    body = resp.json()
    assert body["is_circular"] is True
    assert any("topology" in w for w in body["import_warnings"])


def test_import_rejects_garbage(client):
    resp = client.post(
        "/api/constructs/import",
        files={"file": ("x.txt", b"this is not a sequence file", "text/plain")},
    )
    assert resp.status_code == 422


def test_list_and_get(client):
    cid = import_puc19(client)["id"]
    listing = client.get("/api/constructs").json()
    assert [c["id"] for c in listing] == [cid]
    assert listing[0]["length"] == 2686
    assert listing[0]["operation_count"] == 0
    assert client.get(f"/api/constructs/{cid}").json()["length"] == 2686


def test_get_unknown_construct_is_404(client):
    assert client.get("/api/constructs/nope").status_code == 404


def test_delete_construct(client):
    cid = import_puc19(client)["id"]
    assert client.delete(f"/api/constructs/{cid}").status_code == 204
    assert client.get(f"/api/constructs/{cid}").status_code == 404


# --------------------------------------------------------------------------
# operations
# --------------------------------------------------------------------------

def test_apply_delete_shifts_downstream_features(client):
    before = import_puc19(client)
    cid = before["id"]
    bla_before = next(f for f in before["features"] if f["name"] == "bla"
                      and f["kind"] == "CDS")

    resp = apply(client, cid, "delete", start=600, end=700)
    assert resp.status_code == 201, resp.text
    after = resp.json()

    assert after["length"] == 2686 - 100
    bla_after = next(f for f in after["features"] if f["name"] == "bla"
                     and f["kind"] == "CDS")
    assert bla_after["start"] == bla_before["start"] - 100
    assert bla_after["end"] == bla_before["end"] - 100
    assert after["can_undo"] is True and after["can_redo"] is False


def test_apply_invalid_operation_is_422_and_changes_nothing(client):
    cid = import_puc19(client)["id"]
    resp = apply(client, cid, "delete", start=10, end=99999)
    assert resp.status_code == 422
    assert client.get(f"/api/constructs/{cid}").json()["length"] == 2686
    assert client.get(f"/api/constructs/{cid}/history").json()["operations"] == []


def test_unknown_operation_kind_is_422(client):
    cid = import_puc19(client)["id"]
    resp = client.post(
        f"/api/constructs/{cid}/operations",
        json={"kind": "explode", "payload": {}},
    )
    assert resp.status_code == 422


def test_add_feature_then_export_includes_it(client):
    cid = import_puc19(client)["id"]
    resp = apply(
        client, cid, "add_feature",
        feature={"id": "my-tag", "name": "His tag", "kind": "CDS",
                 "start": 100, "end": 118, "strand": 1, "color": "#3366cc"},
    )
    assert resp.status_code == 201
    assert any(f["name"] == "His tag" for f in resp.json()["features"])
    gb = client.get(f"/api/constructs/{cid}/export?format=genbank").text
    assert "His tag" in gb


# --------------------------------------------------------------------------
# undo / redo / history
# --------------------------------------------------------------------------

def test_undo_and_redo_round_trip(client):
    cid = import_puc19(client)["id"]
    original = client.get(f"/api/constructs/{cid}").json()

    apply(client, cid, "delete", start=600, end=700)
    assert client.get(f"/api/constructs/{cid}").json()["length"] == 2586

    undone = client.post(f"/api/constructs/{cid}/undo").json()
    assert undone["length"] == 2686
    assert undone["sequence"] == original["sequence"]
    assert undone["features"] == original["features"]
    assert undone["can_undo"] is False and undone["can_redo"] is True

    redone = client.post(f"/api/constructs/{cid}/redo").json()
    assert redone["length"] == 2586
    assert redone["can_undo"] is True and redone["can_redo"] is False


def test_undo_keeps_the_operation_in_the_history_as_reverted(client):
    cid = import_puc19(client)["id"]
    apply(client, cid, "delete", start=600, end=700)
    client.post(f"/api/constructs/{cid}/undo")
    history = client.get(f"/api/constructs/{cid}/history").json()
    assert len(history["operations"]) == 1
    assert history["operations"][0]["reverted"] is True
    assert history["can_undo"] is False and history["can_redo"] is True


def test_undo_with_nothing_to_undo_is_409(client):
    cid = import_puc19(client)["id"]
    assert client.post(f"/api/constructs/{cid}/undo").status_code == 409
    assert client.post(f"/api/constructs/{cid}/redo").status_code == 409


def test_a_new_operation_discards_the_redo_stack(client):
    cid = import_puc19(client)["id"]
    apply(client, cid, "delete", start=600, end=700)
    apply(client, cid, "delete", start=100, end=200)
    client.post(f"/api/constructs/{cid}/undo")
    client.post(f"/api/constructs/{cid}/undo")

    apply(client, cid, "insert", pos=0, seq="GGGG")
    history = client.get(f"/api/constructs/{cid}/history").json()
    # Both undone operations are gone for good; only the insert survives.
    assert [o["kind"] for o in history["operations"]] == ["insert"]
    assert [o["index"] for o in history["operations"]] == [0]
    assert history["can_redo"] is False
    assert client.get(f"/api/constructs/{cid}").json()["length"] == 2690


def test_history_preserves_operation_order_and_payloads(client):
    cid = import_puc19(client)["id"]
    apply(client, cid, "insert", pos=10, seq="AAA")
    apply(client, cid, "delete", start=20, end=30)
    ops = client.get(f"/api/constructs/{cid}/history").json()["operations"]
    assert [(o["index"], o["kind"]) for o in ops] == [(0, "insert"), (1, "delete")]
    assert ops[0]["payload"] == {"pos": 10, "seq": "AAA"}


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------

def test_export_genbank_reflects_the_current_state_not_the_base(client):
    cid = import_puc19(client)["id"]
    apply(client, cid, "delete", start=600, end=700)
    resp = client.get(f"/api/constructs/{cid}/export?format=genbank")
    assert resp.status_code == 200
    assert "2586 bp" in resp.text.splitlines()[0]
    assert "circular" in resp.text.splitlines()[0]
    assert "attachment" in resp.headers["content-disposition"]


def test_export_fasta(client):
    cid = import_puc19(client)["id"]
    resp = client.get(f"/api/constructs/{cid}/export?format=fasta")
    assert resp.text.startswith(">")
    assert len("".join(resp.text.splitlines()[1:])) == 2686


def test_export_rejects_an_unknown_format(client):
    cid = import_puc19(client)["id"]
    assert client.get(f"/api/constructs/{cid}/export?format=snapgene").status_code == 422


def test_export_then_reimport_gives_an_equivalent_construct(client):
    cid = import_puc19(client)["id"]
    apply(client, cid, "delete", start=600, end=700)
    original = client.get(f"/api/constructs/{cid}").json()
    exported = client.get(f"/api/constructs/{cid}/export?format=genbank").text

    resp = client.post(
        "/api/constructs/import",
        files={"file": ("rt.gb", exported.encode(), "chemical/x-genbank")},
    )
    reimported = resp.json()
    assert reimported["sequence"] == original["sequence"]
    assert reimported["is_circular"] == original["is_circular"]
    def strip(fs):
        return [{k: v for k, v in f.items() if k != "id"} for f in fs]
    assert strip(reimported["features"]) == strip(original["features"])


# --------------------------------------------------------------------------
# analyses
# --------------------------------------------------------------------------

def test_enzymes_default_to_single_cutters(client):
    cid = import_puc19(client)["id"]
    body = client.get(f"/api/constructs/{cid}/enzymes").json()
    assert all(e["cuts"] == 1 for e in body["enzymes"])
    assert {"EcoRI", "HindIII", "BamHI", "ScaI"} <= {e["name"] for e in body["enzymes"]}


def test_enzymes_all_widens_the_result(client):
    cid = import_puc19(client)["id"]
    default = client.get(f"/api/constructs/{cid}/enzymes").json()["enzymes"]
    widened = client.get(f"/api/constructs/{cid}/enzymes?all=true").json()["enzymes"]
    assert len(widened) > len(default)


def test_enzymes_can_be_filtered_by_name(client):
    cid = import_puc19(client)["id"]
    body = client.get(f"/api/constructs/{cid}/enzymes?names=EcoRI,BamHI").json()
    assert {e["name"] for e in body["enzymes"]} == {"EcoRI", "BamHI"}


def test_enzyme_sites_follow_edits(client):
    cid = import_puc19(client)["id"]
    before = {e["name"]: e["cut_positions"][0]
              for e in client.get(f"/api/constructs/{cid}/enzymes").json()["enzymes"]}
    apply(client, cid, "delete", start=0, end=100)
    after = {e["name"]: e["cut_positions"][0]
             for e in client.get(f"/api/constructs/{cid}/enzymes").json()["enzymes"]}
    assert after["EcoRI"] == before["EcoRI"] - 100
    assert after["ScaI"] == before["ScaI"] - 100


def test_orfs_endpoint_finds_lacz_and_bla(client):
    cid = import_puc19(client)["id"]
    orfs = client.get(f"/api/constructs/{cid}/orfs").json()["orfs"]
    spans = {(o["start"], o["end"], o["strand"]) for o in orfs}
    assert (145, 469, -1) in spans
    assert (1625, 2486, -1) in spans


def test_orfs_min_length_is_honoured(client):
    cid = import_puc19(client)["id"]
    body = client.get(f"/api/constructs/{cid}/orfs?min_length=800").json()
    assert body["min_length"] == 800
    assert all(o["length"] >= 800 for o in body["orfs"])


def test_orfs_rejects_a_nonsense_min_length(client):
    cid = import_puc19(client)["id"]
    assert client.get(f"/api/constructs/{cid}/orfs?min_length=0").status_code == 422


# --------------------------------------------------------------------------
# reading-frame integrity surfaced on the construct
# --------------------------------------------------------------------------

def test_a_clean_puc19_reports_no_frame_issues(client):
    assert import_puc19(client)["frame_issues"] == []


def test_an_out_of_frame_delete_reports_a_blocking_frameshift(client):
    cid = import_puc19(client)["id"]
    body = apply(client, cid, "delete", start=2000, end=2001).json()
    (issue,) = body["frame_issues"]
    assert issue["feature_name"] == "bla"
    assert issue["problem"] == "frameshift"
    assert issue["severity"] == "error"
    assert issue["blocking"] is True


def test_an_in_frame_delete_keeps_the_frame_intact(client):
    cid = import_puc19(client)["id"]
    body = apply(client, cid, "delete", start=2000, end=2003).json()
    assert body["frame_issues"] == []


def test_undo_clears_the_frame_issue(client):
    cid = import_puc19(client)["id"]
    apply(client, cid, "delete", start=2000, end=2001)
    assert client.post(f"/api/constructs/{cid}/undo").json()["frame_issues"] == []


# --------------------------------------------------------------------------
# branching and merging
# --------------------------------------------------------------------------

def branch_of(client, cid: str, name: str = "work") -> dict:
    resp = client.post(f"/api/constructs/{cid}/branch", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def merge(client, cid: str, branch_id: str, **extra):
    return client.post(
        f"/api/constructs/{cid}/merge", json={"branch_id": branch_id, **extra}
    )


def test_a_branch_starts_identical_to_its_parent(client):
    parent = import_puc19(client)
    apply(client, parent["id"], "delete", start=600, end=700)
    parent = client.get(f"/api/constructs/{parent['id']}").json()

    child = branch_of(client, parent["id"])
    assert child["parent_id"] == parent["id"]
    assert child["sequence"] == parent["sequence"]
    assert child["features"] == parent["features"]
    # The parent's history came along, so the branch can undo it too.
    assert child["can_undo"] is True
    assert len(client.get(f"/api/constructs/{child['id']}/history").json()
               ["operations"]) == 1


def test_branches_are_listed_with_how_far_ahead_they_are(client):
    parent = import_puc19(client)
    child = branch_of(client, parent["id"], "cassette swap")
    apply(client, child["id"], "delete", start=100, end=200)
    apply(client, child["id"], "insert", pos=50, seq="GGGG")

    (listed,) = client.get(f"/api/constructs/{parent['id']}/branches").json()
    assert listed["name"] == "cassette swap"
    assert listed["ahead"] == 2


def test_editing_a_branch_does_not_touch_the_parent(client):
    parent = import_puc19(client)
    child = branch_of(client, parent["id"])
    apply(client, child["id"], "delete", start=100, end=200)
    assert client.get(f"/api/constructs/{parent['id']}").json()["length"] == 2686
    assert client.get(f"/api/constructs/{child['id']}").json()["length"] == 2586


def test_merging_non_overlapping_edits_applies_both(client):
    parent = import_puc19(client)
    cid = parent["id"]
    child = branch_of(client, cid)

    apply(client, cid, "delete", start=2400, end=2500)      # parent edits late
    apply(client, child["id"], "delete", start=100, end=200)  # branch edits early

    merged = merge(client, cid, child["id"])
    assert merged.status_code == 200, merged.text
    body = merged.json()
    assert body["length"] == 2686 - 100 - 100
    history = client.get(f"/api/constructs/{cid}/history").json()
    assert [o["kind"] for o in history["operations"]] == ["delete", "delete"]
    assert [o["index"] for o in history["operations"]] == [0, 1]


def test_a_merged_operation_is_rebased_not_copied_verbatim(client):
    parent = import_puc19(client)
    cid = parent["id"]
    child = branch_of(client, cid)
    apply(client, cid, "insert", pos=0, seq="GGGG")            # shifts everything
    apply(client, child["id"], "delete", start=1000, end=1100)

    assert merge(client, cid, child["id"]).status_code == 200
    ops = client.get(f"/api/constructs/{cid}/history").json()["operations"]
    assert ops[1]["payload"] == {"start": 1004, "end": 1104}


def test_overlapping_edits_are_refused_with_the_conflict_listed(client):
    parent = import_puc19(client)
    cid = parent["id"]
    child = branch_of(client, cid)
    apply(client, cid, "delete", start=1000, end=1200)
    apply(client, child["id"], "delete", start=1100, end=1300)

    resp = merge(client, cid, child["id"])
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["clean"] is False
    assert [c["reason"] for c in detail["conflicts"]] == ["overlapping_edit"]
    # Nothing was written.
    assert client.get(f"/api/constructs/{cid}").json()["length"] == 2686 - 200


def test_preview_reports_without_writing(client):
    parent = import_puc19(client)
    cid = parent["id"]
    child = branch_of(client, cid)
    apply(client, cid, "delete", start=2400, end=2500)
    apply(client, child["id"], "delete", start=100, end=200)

    preview = client.post(
        f"/api/constructs/{cid}/merge/preview", json={"branch_id": child["id"]}
    ).json()
    assert preview["clean"] is True
    assert preview["rebased"] == 1
    assert client.get(f"/api/constructs/{cid}").json()["length"] == 2586


def test_merging_something_that_is_not_a_branch_is_422(client):
    a = import_puc19(client)
    b = import_puc19(client)
    assert merge(client, a["id"], b["id"]).status_code == 422


def test_a_merge_that_breaks_a_reading_frame_is_refused(client):
    """Two edits that merge cleanly and jointly ruin the protein."""
    #  ATG + 4 Pro codons + TAA, then filler.
    seq = "ATG" + "CCC" * 4 + "TAA" + "GGG" * 10
    created = client.post(
        "/api/constructs",
        json={
            "name": "reporter", "sequence": seq, "is_circular": True,
            "features": [{"id": "cds", "name": "gfp", "kind": "CDS",
                          "start": 0, "end": 18, "strand": 1}],
        },
    ).json()
    cid = created["id"]
    child = branch_of(client, cid)

    assert apply(client, cid, "insert", pos=4, seq="CCT").json()["frame_issues"] == []
    assert apply(client, child["id"], "insert", pos=4,
                 seq="AAG").json()["frame_issues"] == []

    resp = merge(client, cid, child["id"])
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["conflicts"] == []            # coordinates were fine ...
    (issue,) = detail["new_frame_issues"]        # ... the biology was not
    assert issue["problem"] == "premature_stop"
    assert issue["codon"] == 3
    assert issue["blocking"] is True

    # The target is untouched by the refusal: its own 3 bp insert and nothing
    # of the branch's.
    after = client.get(f"/api/constructs/{cid}").json()
    assert after["length"] == len(seq) + 3
    assert after["frame_issues"] == []


def test_a_frame_breaking_merge_can_be_forced(client):
    seq = "ATG" + "CCC" * 4 + "TAA" + "GGG" * 10
    cid = client.post(
        "/api/constructs",
        json={"name": "reporter", "sequence": seq, "is_circular": True,
              "features": [{"id": "cds", "name": "gfp", "kind": "CDS",
                            "start": 0, "end": 18, "strand": 1}]},
    ).json()["id"]
    child = branch_of(client, cid)
    apply(client, cid, "insert", pos=4, seq="CCT")
    apply(client, child["id"], "insert", pos=4, seq="AAG")

    forced = merge(client, cid, child["id"], allow_frame_breaks=True)
    assert forced.status_code == 200
    assert [i["problem"] for i in forced.json()["frame_issues"]] == [
        "premature_stop"
    ]


def test_undoing_below_the_fork_point_blocks_the_merge(client):
    parent = import_puc19(client)
    cid = parent["id"]
    apply(client, cid, "delete", start=600, end=700)
    child = branch_of(client, cid)          # forked with one shared operation
    apply(client, child["id"], "delete", start=100, end=200)

    client.post(f"/api/constructs/{cid}/undo")   # rewrites the shared history
    resp = merge(client, cid, child["id"])
    assert resp.status_code == 409
    assert "fork" in resp.json()["detail"].lower()


# --------------------------------------------------------------------------
# diffing two constructs
# --------------------------------------------------------------------------

def diff(client, cid: str, against: str):
    return client.get(f"/api/constructs/{cid}/diff?against={against}")


def test_a_fresh_branch_differs_in_nothing(client):
    parent = import_puc19(client)
    child = branch_of(client, parent["id"])

    body = diff(client, parent["id"], child["id"]).json()
    assert body["relationship"] == "branch"
    assert body["sequence"]["identical"] is True
    assert body["sequence"]["identity"] == 1.0
    assert body["features"]["unchanged"] == 18
    assert body["features"]["added"] == body["features"]["removed"] == []
    assert body["operations"]["left_only"] == body["operations"]["right_only"] == []


def test_a_diff_localises_an_edit_and_names_the_features_it_hit(client):
    parent = import_puc19(client)
    cid = parent["id"]
    child = branch_of(client, cid)
    apply(client, child["id"], "delete", start=200, end=700)

    body = diff(client, cid, child["id"]).json()
    seq = body["sequence"]
    assert seq["identical"] is False
    assert seq["bases_removed"] == 500 and seq["bases_added"] == 0
    (change,) = [s for s in seq["segments"] if s["op"] != "equal"]
    assert change["op"] == "delete"
    assert (change["left_start"], change["left_end"]) == (200, 700)

    features = body["features"]
    assert "lacZalpha" in {f["name"] for f in features["removed"]}
    #  Features the cut crossed are changed ...
    assert any(c["after"]["truncated"] for c in features["changed"])
    #  ... while everything downstream merely moved, and is reported apart so
    #  it does not bury the features that actually changed.
    assert features["shifted"]
    assert all(
        c["changed_fields"] == ["start", "end"] for c in features["shifted"]
    )
    assert all(not c["after"]["truncated"] for c in features["shifted"])


def test_the_diff_reports_which_operations_each_side_ran(client):
    parent = import_puc19(client)
    cid = parent["id"]
    apply(client, cid, "delete", start=2400, end=2500)   # before the fork
    child = branch_of(client, cid)

    apply(client, cid, "insert", pos=0, seq="GGGG")
    apply(client, child["id"], "delete", start=100, end=200)
    apply(client, child["id"], "delete", start=300, end=400)

    ops = diff(client, cid, child["id"]).json()["operations"]
    assert ops["shared"] == 1
    assert [o["kind"] for o in ops["left_only"]] == ["insert"]
    assert [o["kind"] for o in ops["right_only"]] == ["delete", "delete"]


def test_a_rotation_reads_as_an_origin_shift_not_a_rewrite(client):
    parent = import_puc19(client)
    cid = parent["id"]
    child = branch_of(client, cid)
    apply(client, child["id"], "set_origin", pos=1500)

    body = diff(client, cid, child["id"]).json()
    assert body["sequence"]["identical"] is True
    assert body["sequence"]["origin_shift"] == 2686 - 1500
    # The molecule is unchanged, so no feature should read as moved.
    assert body["features"]["changed"] == []
    assert body["features"]["unchanged"] == 18


def test_unrelated_constructs_can_still_be_compared(client):
    a = import_puc19(client)
    b = client.post(
        "/api/constructs", json={"name": "other", "sequence": "ACGT" * 50}
    ).json()

    body = diff(client, a["id"], b["id"]).json()
    assert body["relationship"] == "unrelated"
    assert body["operations"]["shared"] == 0
    assert body["sequence"]["identical"] is False


def test_the_diff_is_symmetric_about_which_side_is_added(client):
    parent = import_puc19(client)
    cid = parent["id"]
    child = branch_of(client, cid)
    apply(client, child["id"], "insert", pos=500, seq="TTTTTTTTTT")

    forward = diff(client, cid, child["id"]).json()["sequence"]
    backward = diff(client, child["id"], cid).json()["sequence"]
    assert forward["bases_added"] == backward["bases_removed"] == 10
    assert forward["bases_removed"] == backward["bases_added"] == 0


def test_diffing_a_construct_against_itself_is_422(client):
    cid = import_puc19(client)["id"]
    assert diff(client, cid, cid).status_code == 422


def test_diffing_against_something_that_does_not_exist_is_404(client):
    cid = import_puc19(client)["id"]
    assert diff(client, cid, "nope").status_code == 404


def test_a_refused_merge_still_shows_what_it_would_have_produced(client):
    """Refusing without showing the damage would defeat the point."""
    record = (DATA / "puc19_annotated.gb").read_bytes()
    parent = client.post(
        "/api/constructs/import",
        files={"file": ("puc19.gb", record, "chemical/x-genbank")},
    ).json()
    cid = parent["id"]
    child = branch_of(client, cid, "AmpR +Cys")

    # Both edits add one codon to bla at the same site; each is clean.
    assert apply(client, cid, "insert", pos=2001,
                 seq="AAT").json()["frame_issues"] == []
    assert apply(client, child["id"], "insert", pos=2001,
                 seq="CAG").json()["frame_issues"] == []

    detail = merge(client, cid, child["id"]).json()["detail"]
    (issue,) = detail["new_frame_issues"]
    assert issue["feature_name"] == "bla" and issue["codon"] == 163

    # Enough to draw the break on the map without guessing.
    assert (issue["stop_start"], issue["stop_end"]) == (2003, 2006)
    assert issue["translated_end"] - issue["translated_start"] == 162 * 3
    assert detail["merged_length"] == 2692
    assert detail["merged_sequence"][2003:2006] == "TCA"


def test_a_clean_preview_also_carries_the_merged_sequence(client):
    cid = import_puc19(client)["id"]
    child = branch_of(client, cid)
    apply(client, child["id"], "delete", start=100, end=200)
    preview = client.post(
        f"/api/constructs/{cid}/merge/preview", json={"branch_id": child["id"]}
    ).json()
    assert preview["clean"] is True
    assert preview["merged_length"] == 2586
    assert len(preview["merged_sequence"]) == 2586


def test_a_conflicted_merge_has_no_merged_sequence_to_show(client):
    cid = import_puc19(client)["id"]
    child = branch_of(client, cid)
    apply(client, cid, "delete", start=1000, end=1200)
    apply(client, child["id"], "delete", start=1100, end=1300)
    detail = merge(client, cid, child["id"]).json()["detail"]
    assert detail["conflicts"]
    assert detail["merged_sequence"] is None


def test_the_preview_carries_the_features_as_the_merge_would_rebase_them(client):
    cid = import_puc19(client)["id"]
    child = branch_of(client, cid)
    apply(client, cid, "insert", pos=0, seq="GGGG")
    apply(client, child["id"], "delete", start=1000, end=1100)

    preview = client.post(
        f"/api/constructs/{cid}/merge/preview", json={"branch_id": child["id"]}
    ).json()
    bla = next(
        f for f in preview["merged_features"]
        if f["name"] == "bla" and f["kind"] == "CDS"
    )
    # +4 from the parent's insert, -100 from the branch's delete.
    assert (bla["start"], bla["end"]) == (1625 + 4 - 100, 2486 + 4 - 100)


def test_merging_the_same_branch_twice_does_not_apply_it_twice(client):
    """A branch that has been merged is no longer ahead of its parent."""
    cid = import_puc19(client)["id"]
    child = branch_of(client, cid)
    apply(client, child["id"], "insert", pos=500, seq="TTTTTTTTTT")

    first = merge(client, cid, child["id"])
    assert first.status_code == 200
    assert first.json()["length"] == 2696

    listed = client.get(f"/api/constructs/{cid}/branches").json()
    assert listed[0]["ahead"] == 0, "a merged branch is not ahead any more"

    second = merge(client, cid, child["id"])
    assert client.get(f"/api/constructs/{cid}").json()["length"] == 2696, (
        "the branch's edit was applied a second time"
    )
    assert second.status_code in (200, 409)


def test_a_branch_can_keep_working_after_being_merged(client):
    """Only the work done since the last merge crosses over."""
    cid = import_puc19(client)["id"]
    child = branch_of(client, cid)

    apply(client, child["id"], "insert", pos=500, seq="AAAA")
    assert merge(client, cid, child["id"]).status_code == 200
    assert client.get(f"/api/constructs/{cid}").json()["length"] == 2690

    apply(client, child["id"], "insert", pos=800, seq="GG")
    assert client.get(f"/api/constructs/{cid}/branches").json()[0]["ahead"] == 1

    assert merge(client, cid, child["id"]).status_code == 200
    assert client.get(f"/api/constructs/{cid}").json()["length"] == 2692
    history = client.get(f"/api/constructs/{cid}/history").json()
    assert [o["kind"] for o in history["operations"]] == ["insert", "insert"]


# ---------------------------------------------------------------------------
# suppressing a finding
# ---------------------------------------------------------------------------

#: Filler, a bare upstream window, a CDS, filler. No Shine-Dalgarno anywhere.
LINTABLE = "A" * 20 + "C" * 12 + "ATGAAAGGGCCCTAA" + "T" * 13


def lintable_construct(client) -> dict:
    created = client.post(
        "/api/constructs",
        json={"name": "lintable", "sequence": LINTABLE, "is_circular": True},
    )
    assert created.status_code == 201, created.text
    construct_id = created.json()["id"]
    annotated = client.post(
        f"/api/constructs/{construct_id}/operations",
        json={
            "kind": "add_feature",
            "payload": {
                "feature": {
                    "id": "cds",
                    "name": "lacZalpha",
                    "kind": "CDS",
                    "start": 32,
                    "end": 47,
                    "strand": 1,
                }
            },
        },
    )
    assert annotated.status_code == 201, annotated.text
    return annotated.json()


def rbs_finding(detail: dict) -> dict:
    return next(f for f in detail["findings"] if f["rule_id"] == "rbs-atg-spacing")


def test_a_construct_names_the_pack_that_judged_it(client):
    detail = lintable_construct(client)
    pack = detail["rule_pack"]
    assert len(pack["digest"]) == 64
    assert pack["rules"] == 3 and pack["errors"] == []
    # And every finding carries it, so a decision can record which rules it
    # was taken against.
    assert {f["pack_digest"] for f in detail["findings"]} == {pack["digest"]}


def test_a_construct_carries_its_design_rule_findings(client):
    finding = rbs_finding(lintable_construct(client))
    assert finding["severity"] == "error"
    assert finding["suppressed"] is False
    # The engine hands back the evidence it read, so the client never has to
    # work out which bases the rule looked at.
    assert finding["window"]["digest"]
    assert finding["rule_digest"]


def test_suppressing_a_finding_marks_it_without_hiding_it(client):
    detail = lintable_construct(client)
    finding = rbs_finding(detail)
    response = client.post(
        f"/api/constructs/{detail['id']}/operations",
        json={
            "kind": "suppress_finding",
            "payload": {
                "rule_id": finding["rule_id"],
                "feature_id": finding["feature_id"],
                "reason": "weak RBS on purpose, titrating expression",
                "window": finding["window"],
                "rule_digest": finding["rule_digest"],
            },
        },
    )
    assert response.status_code == 201, response.text
    suppressed = rbs_finding(response.json())
    assert suppressed["suppressed"] is True
    assert suppressed["suppression"]["reason"].startswith("weak RBS")
    assert suppressed["suppression"]["stale"] is False

    # It is an operation, so undo lifts it like any other edit.
    undone = client.post(f"/api/constructs/{detail['id']}/undo")
    assert rbs_finding(undone.json())["suppressed"] is False


def test_a_suppression_without_a_reason_is_refused(client):
    detail = lintable_construct(client)
    finding = rbs_finding(detail)
    response = client.post(
        f"/api/constructs/{detail['id']}/operations",
        json={
            "kind": "suppress_finding",
            "payload": {
                "rule_id": finding["rule_id"],
                "feature_id": finding["feature_id"],
                "reason": "",
                "window": finding["window"],
                "rule_digest": finding["rule_digest"],
            },
        },
    )
    assert response.status_code == 422
    assert "reason" in response.json()["detail"]


def test_editing_the_window_brings_a_suppressed_finding_back(client):
    detail = lintable_construct(client)
    finding = rbs_finding(detail)
    client.post(
        f"/api/constructs/{detail['id']}/operations",
        json={
            "kind": "suppress_finding",
            "payload": {
                "rule_id": finding["rule_id"],
                "feature_id": finding["feature_id"],
                "reason": "weak RBS on purpose, titrating expression",
                "window": finding["window"],
                "rule_digest": finding["rule_digest"],
            },
        },
    )
    edited = client.post(
        f"/api/constructs/{detail['id']}/operations",
        json={"kind": "replace", "payload": {"start": 24, "end": 27, "seq": "GGG"}},
    )
    back = rbs_finding(edited.json())
    assert back["suppressed"] is False
    assert back["suppression"]["stale"] is True
    assert back["suppression"]["was"] != back["suppression"]["now"]


# ---------------------------------------------------------------------------
# the design-rule gate on a merge, and the one door through it
# ---------------------------------------------------------------------------

WEAK_RBS = "weak RBS on purpose, we are titrating expression"


def revived_merge(client) -> tuple[str, str]:
    """A parent and a branch whose merge reopens a decision somebody made.

    The branch annotated a CDS with a deliberately weak ribosome binding site
    and wrote down why. The parent rewrote the bases that decision was about.
    Neither side is broken on its own.
    """
    parent = client.post(
        "/api/constructs",
        json={"name": "pDemo", "sequence": LINTABLE, "is_circular": True},
    ).json()
    child = branch_of(client, parent["id"], "weak RBS")
    annotated = apply(
        client,
        child["id"],
        "add_feature",
        feature={
            "id": "cds",
            "name": "lacZalpha",
            "kind": "CDS",
            "start": 32,
            "end": 47,
            "strand": 1,
        },
    ).json()
    finding = rbs_finding(annotated)
    assert apply(
        client,
        child["id"],
        "suppress_finding",
        rule_id=finding["rule_id"],
        feature_id=finding["feature_id"],
        reason=WEAK_RBS,
        window=finding["window"],
        rule_digest=finding["rule_digest"],
    ).status_code == 201
    assert apply(
        client, parent["id"], "replace", start=20, end=23, seq="GGG"
    ).status_code == 201
    return parent["id"], child["id"]


def test_a_merge_that_revives_a_documented_decision_is_refused(client):
    cid, bid = revived_merge(client)
    response = merge(client, cid, bid)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["clean"] is False
    assert detail["conflicts"] == []  # the coordinates merged fine

    # The finding carries its own explanation, down to both readings of the
    # window, so the UI can say why this bounced without asking anything else.
    # The refusal names the rules it was judged against: a gate that can move
    # on the server without saying so stops being trusted.
    parent = client.get(f"/api/constructs/{cid}").json()
    assert detail["rule_pack"]["digest"] == parent["rule_pack"]["digest"]

    (blocked,) = detail["new_findings"]
    assert blocked["rule_id"] == "rbs-atg-spacing"
    assert blocked["feature_name"] == "lacZalpha"
    assert blocked["suppression"]["reason"] == WEAK_RBS
    assert blocked["suppression"]["stale"] is True
    assert blocked["suppression"]["was"] != blocked["suppression"]["now"]


def test_the_frame_override_does_not_open_the_rule_gate(client):
    """Different gates, different keys.

    A frameshift mutant is real work somebody may be doing on purpose, so that
    gate has a flag. A design-rule error opens only against a reason in the
    log.
    """
    cid, bid = revived_merge(client)
    assert merge(client, cid, bid, allow_frame_breaks=True).status_code == 409


def test_recording_the_decision_at_merge_time_lets_it_through(client):
    cid, bid = revived_merge(client)
    merged = merge(
        client,
        cid,
        bid,
        suppress=[
            {
                "rule_id": "rbs-atg-spacing",
                "feature_id": "cds",
                "reason": "still deliberate, checked against the new upstream",
            }
        ],
    )
    assert merged.status_code == 200, merged.text
    finding = rbs_finding(merged.json())
    assert finding["suppressed"] is True
    assert finding["suppression"]["stale"] is False
    assert finding["suppression"]["reason"].startswith("still deliberate")

    # And it went through the front door: the decision is an operation in the
    # merge commit, not a flag on the request. Both decisions are in the log,
    # in the order they were taken - the branch's original call, then the one
    # somebody made looking at the merged sequence.
    history = client.get(f"/api/constructs/{cid}/history").json()
    recorded = [o for o in history["operations"] if o["kind"] == "suppress_finding"]
    assert [o["payload"]["reason"] for o in recorded] == [
        WEAK_RBS,
        "still deliberate, checked against the new upstream",
    ]


def test_suppressing_something_that_is_not_blocking_the_merge_is_refused(client):
    cid, bid = revived_merge(client)
    response = merge(
        client,
        cid,
        bid,
        suppress=[
            {
                "rule_id": "cds-without-terminator",
                "feature_id": "cds",
                "reason": "this warning is not what is blocking anything",
            }
        ],
    )
    assert response.status_code == 422
    assert "not blocking" in response.json()["detail"]


#: Well spaced, translating cleanly. Each branch will break one thing.
BOTH_SEQ = "TTTT" + "AGGAGG" + "T" * 8 + "ATG" + "CCC" * 4 + "TAA" + "T" * 24


def both_gates(client) -> tuple[str, str]:
    parent = client.post(
        "/api/constructs",
        json={"name": "pBoth", "sequence": BOTH_SEQ, "is_circular": True},
    ).json()
    cid = parent["id"]
    assert apply(
        client,
        cid,
        "add_feature",
        feature={
            "id": "cds",
            "name": "gfp",
            "kind": "CDS",
            "start": 18,
            "end": 36,
            "strand": 1,
        },
    ).status_code == 201
    child = branch_of(client, cid, "one more codon")
    for target_id, gap, codon in (
        (cid, 14, "AAT"),
        (child["id"], 12, "AAA"),
    ):
        assert apply(client, target_id, "insert", pos=gap, seq="TTT").status_code == 201
        assert apply(client, target_id, "insert", pos=25, seq=codon).status_code == 201
    return cid, child["id"]


def test_a_merge_that_fails_both_gates_reports_both_in_one_refusal(client):
    """So the UI can put both in one dialog, and settle them in one round."""
    cid, bid = both_gates(client)
    detail = merge(client, cid, bid).json()["detail"]
    assert detail["conflicts"] == []
    assert [i["problem"] for i in detail["new_frame_issues"]] == ["premature_stop"]
    assert [f["rule_id"] for f in detail["new_findings"]] == ["rbs-atg-spacing"]


def test_both_gates_clear_in_a_single_request(client):
    cid, bid = both_gates(client)
    merged = merge(
        client,
        cid,
        bid,
        allow_frame_breaks=True,
        suppress=[
            {
                "rule_id": "rbs-atg-spacing",
                "feature_id": "cds",
                "reason": "truncation and weak initiation are both intended here",
            }
        ],
    )
    assert merged.status_code == 200, merged.text
    body = merged.json()
    assert [i["problem"] for i in body["frame_issues"]] == ["premature_stop"]
    assert rbs_finding(body)["suppressed"] is True
