"""Version Evolution Explorer: acceptance cases A, B and C and the import-coverage expectations, checked against the
REAL store through the explorer's own read-only path (`mode=ro`; the conftest guard allows nothing else).

The production file is opened here on purpose -- read-only is exactly what is being verified -- and its bytes are
compared before and after. The tests skip when the store is absent or has not been migrated to schema v8 (details
tables); they never migrate anything.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch.explorer import EvolutionExplorer, graph  # noqa: E402
from qch.storage.sqlite.readonly import open_read_only  # noqa: E402

PRODUCTION = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"  # deliberately not named REAL_DB: conftest must not redirect this one


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def ex():
    if not PRODUCTION.exists():
        pytest.skip("production store not present")
    before = _sha(PRODUCTION)
    with EvolutionExplorer(open_read_only(PRODUCTION)) as explorer:
        if not explorer.capabilities().source_details:
            pytest.skip("store is not at schema v8 (submission source details)")
        yield explorer
    assert _sha(PRODUCTION) == before  # opening, searching, expanding and rendering details changed nothing


def version_id(ex, key: str) -> str:
    (match,) = [m for m, _ in ex.search(key).matches if m.external_version_key == key]
    return match.version_id


# == case A: ecdsafail:31f9c58 ===================================================================


def test_case_a_search_by_key_and_by_commit(ex):
    by_key = ex.search("ecdsafail:31f9c58")
    by_commit = ex.search("31f9c58")
    assert [m.external_version_key for m, _ in by_key.matches] == [m.external_version_key for m, _ in by_commit.matches] == ["ecdsafail:31f9c58"]
    assert by_key.matches[0][1] == "external_version_key" and by_commit.matches[0][1] == "commit id / partial key"


def test_case_a_default_neighborhood_is_five_nodes_and_four_edges_with_the_expected_relations(ex):
    origin = version_id(ex, "ecdsafail:31f9c58")
    view = graph.project(ex.start(origin))
    names = {n.version_id: n.external_version_key.split(":")[1] for n in view.nodes}
    assert sorted(names.values()) == ["0422437", "22a6422", "31f9c58", "508d24b", "897dda2"] and len(view.edges) == 4
    relations = sorted((names[e.source_version_id], names[e.target_version_id], e.relation_type) for e in view.edges)
    assert relations == [
        ("0422437", "31f9c58", "next_promoted_commit"),
        ("31f9c58", "22a6422", "branched_from"),
        ("31f9c58", "508d24b", "branched_from"),
        ("31f9c58", "897dda2", "next_promoted_commit"),
    ]
    by_name = {names[n.version_id]: n for n in view.nodes}
    expected = {"0422437": (1153, 1281867, 1477992651, "accepted"), "31f9c58": (1150, 1284776, 1477492400, "accepted"),
                "22a6422": (1150, 1284776, 1477492400, "rejected"), "508d24b": (1150, 1284776, 1477492400, "rejected"),
                "897dda2": (1321, 952707, 1258525947, "accepted")}
    for name, (q, t, s, status) in expected.items():
        node = by_name[name]
        assert (node.official.peak_qubits, node.official.avg_executed_toffoli, node.official.score, node.platform_status) == (q, t, s, status), name
    assert view.origin == origin and by_name["31f9c58"].version_id == origin


def test_case_a_edge_31f9c58_to_897dda2_deltas_percentage_no_label_and_a_submitter_note(ex):
    origin = version_id(ex, "ecdsafail:31f9c58")
    edge = next(e for e in ex.edges_for(origin, "out", ("next_promoted_commit",)))
    detail = ex.edge_detail(edge.edge_id)
    assert (detail.source.external_version_key, detail.target.external_version_key) == ("ecdsafail:31f9c58", "ecdsafail:897dda2")
    assert (detail.official.q_delta, detail.official.t_delta, detail.official.s_delta) == (171, -332069, -218966453)
    assert round(detail.official.score_reduction_percent, 4) == 14.8201
    assert detail.linked_labels == ()  # no author classification is linked to this edge, and none is inferred
    assert detail.target_detail.submitter.handle == "teddyjfpender"
    (note,) = [d for d in detail.target_detail.source_details if d.is_latest]
    assert note.note_presence == "value" and note.note_chars == 7309 and note.note.startswith("Model: GPT-5.6 Sol")


def test_case_a_timestamps_keep_their_three_meanings(ex):
    detail = ex.version_detail(version_id(ex, "ecdsafail:31f9c58"))
    assert detail.evaluation.platform_created_at == "2026-08-20T07:30:16.707Z" and detail.evaluation.promotion_finished_at == "2026-08-20T07:32:33.479Z"
    assert detail.summary.historical_time == "2026-08-20T07:30:42Z"  # Git author date: a different clock, later than the platform's creation time
    assert detail.discovered_at.startswith("2026-09-21T") and detail.discovered_at != detail.summary.historical_time  # QCH ingestion


# == case B: ecdsafail:9700396 ===================================================================


def test_case_b_identity_submitter_scores_and_timestamps(ex):
    detail = ex.version_detail(version_id(ex, "ecdsafail:9700396"))
    assert detail.summary.submission_id == "ebb6a943f55c4497a7d0b39678813ff8"
    assert detail.submission_key == "ecdsafail:3ccbe24c-0429-4ea9-972a-80afb1421679"
    assert detail.submitter.handle == "jackylee0424" and detail.submitter.evidence_class == "AUTHORITATIVE"
    assert detail.evaluation.official.score == 1137367864 and detail.summary.official.score == 1137367864
    latest = next(d for d in detail.source_details if d.is_latest)
    assert (latest.platform_created_at_raw, latest.platform_updated_at_raw) == ("2026-09-11T18:06:20.877Z", "2026-09-11T18:08:16.628Z")
    assert (detail.evaluation.platform_created_at, detail.evaluation.platform_updated_at) == (latest.platform_created_at_raw, latest.platform_updated_at_raw)
    assert detail.technique_labels == () and "No author-assigned technique label" in detail.unavailable["technique_labels"]


def test_case_b_long_note_claimed_score_and_coauthors(ex):
    latest = next(d for d in ex.version_detail(version_id(ex, "ecdsafail:9700396")).source_details if d.is_latest)
    assert latest.note_chars == len(latest.note) == 8620
    assert hashlib.sha256(latest.note.encode("utf-8")).hexdigest() == latest.note_sha256 == "f906eac274b0ce44eae3e5be9c44c23028637f9a10d40a5d7f6f759daa61124f"
    assert (latest.claimed_score_presence, latest.claimed_score_json) == ("null", None)  # an explicit null, not zero and not absent
    assert (latest.coauthors_presence, latest.coauthors_json) == ("empty", "[]")
    assert latest.note.startswith("Model: GPT-6") and latest.note.endswith("inputs.\r\n")


def test_case_b_high_degree_version_respects_the_node_limit(ex):
    state = ex.start(version_id(ex, "ecdsafail:9700396"), max_nodes=25)
    assert len(state.nodes) == 25 and graph.neighbors_hidden_by_limit(state) == 21 and len(graph.project(state).edges) == 24


# == case C: ecdsafail:0780f67 ===================================================================


def test_case_c_incoming_mainline_edge_label_and_note_as_separate_evidence(ex):
    target = version_id(ex, "ecdsafail:0780f67")
    (edge,) = [e for e in ex.edges_for(target, "in", ("next_promoted_commit",))]
    detail = ex.edge_detail(edge.edge_id)
    assert detail.source.external_version_key == "ecdsafail:cc5584e" and detail.target.external_version_key == "ecdsafail:0780f67"
    (label,) = detail.linked_labels
    assert (label.family, label.change_type) == ("Windowing", "K2 transcript pair-compressor")
    assert label.source_file_path == "appendices/tex/appendix-C-content.tex" and label.source_repository_commit == "ef7ecd2b27cf27881874468e003e53e16ba48075"
    assert label.classification_status.startswith("author_final_primary_label") and label.incoming_edge_id == edge.edge_id
    note = next(d for d in detail.target_detail.source_details if d.is_latest)
    assert note.note_presence == "value" and note.note.startswith("Model: Claude Opus 4.8")
    assert len(detail.target_detail.source_details) == 2  # the July and the September snapshot, each available on its own
    assert {d.source_fetched_at for d in detail.target_detail.source_details} == {"2026-07-27T16:16:01Z", "2026-09-24T07:21:24Z"}


# == import-coverage expectations (verification only; none of these numbers is hard-coded in the UI) ==


def test_import_coverage_matches_the_documented_expectations(ex):
    con = sqlite3.connect(PRODUCTION.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        assert con.execute("SELECT count(*) FROM submission_source_detail").fetchone()[0] == 2079
        assert con.execute("SELECT count(*) FROM submission_detail_source").fetchone()[0] == 2
        population = con.execute("SELECT count(*) FROM submission WHERE source_system = 'ecdsafail'").fetchone()[0]
        rows = dict(con.execute(
            "SELECT coalesce(l.note_presence, 'no snapshot') AS presence, count(*) FROM submission s"
            " LEFT JOIN submission_source_detail_latest l ON l.submission_id = s.submission_id WHERE s.source_system = 'ecdsafail' GROUP BY 1").fetchall())
        assert population == 1003 and rows == {"value": 987, "null": 12, "no snapshot": 4}
        assert con.execute("SELECT count(*) FROM version_technique_label").fetchone()[0] == 404
    finally:
        con.close()


def test_the_explorer_reports_the_same_coverage_version_by_version(ex):
    assert ex.capabilities().schema_version >= 8 and ex.capabilities().technique_labels and ex.capabilities().contributors
    labelled = ex.version_detail(version_id(ex, "ecdsafail:0780f67")).technique_labels
    assert [t.family for t in labelled] == ["Windowing"]
