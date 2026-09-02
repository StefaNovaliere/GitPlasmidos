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
    strip = lambda fs: [
        {k: v for k, v in f.items() if k != "id"} for f in fs
    ]
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
