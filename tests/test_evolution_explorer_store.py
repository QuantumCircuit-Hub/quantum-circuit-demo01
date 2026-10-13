"""Version Evolution Explorer: the read-only SQLite reader and the EvolutionExplorer facade, on a small
synthetic store (tests/explorer_fixtures.py). Temporary files only."""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import explorer_fixtures as fx  # noqa: E402
from qch.explorer import EvolutionExplorer, graph  # noqa: E402
from qch.storage.sqlite.readonly import StoreUnavailable, open_read_only  # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    path = tmp_path_factory.mktemp("explorer") / "full.sqlite3"
    return path, fx.build_explorer_db(path)


@pytest.fixture
def ex(built):
    path, _ = built
    with EvolutionExplorer(open_read_only(path)) as explorer:
        yield explorer


@pytest.fixture
def ids(built):
    return built[1]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def matches(result):
    return [m.external_version_key or m.version_label for m, _ in result.matches]


# == read-only access ============================================================================


def test_the_reader_cannot_write_and_leaves_the_file_byte_identical(built):
    path, ids = built
    before = sha(path)
    store = open_read_only(path)
    try:
        for statement in ("CREATE TABLE must_not_exist (x)", "DELETE FROM circuit_version", "UPDATE artifact SET status = 'FAILED'", "INSERT INTO schema_migrations VALUES (99, 'x')"):
            with pytest.raises(sqlite3.OperationalError):  # mode=ro + query_only: SQLite itself refuses
                store._conn.execute(statement)
        EvolutionExplorer(store).edge_detail(ids["e_ab_next"])
        EvolutionExplorer(store).version_detail(ids["b"])
    finally:
        store.close()
    assert sha(path) == before


def test_opening_does_not_apply_pending_migrations(tmp_path):
    path = tmp_path / "old.sqlite3"
    fx.build_explorer_db(path)
    conn = sqlite3.connect(str(path))
    conn.execute("DELETE FROM schema_migrations WHERE version = 8")  # simulate a store that has not been migrated to v8 yet
    conn.commit()
    conn.close()
    before = sha(path)
    with open_read_only(path) as store:
        assert store.capabilities().schema_version == 7
    assert sha(path) == before  # QCH.open() would have re-applied migration 008 here; the explorer path never does
    conn = sqlite3.connect(str(path))
    assert conn.execute("SELECT count(*) FROM schema_migrations WHERE version = 8").fetchone()[0] == 0
    conn.close()


def test_missing_unreadable_and_non_qch_databases_raise_a_helpful_error(tmp_path):
    with pytest.raises(StoreUnavailable, match="QCH_DB_PATH"):
        open_read_only(tmp_path / "missing.sqlite3")
    with pytest.raises(StoreUnavailable, match="not a file"):
        open_read_only(tmp_path)
    garbage = tmp_path / "garbage.sqlite3"
    garbage.write_bytes(b"this is not a database" * 100)
    with pytest.raises(StoreUnavailable, match="QCH_DB_PATH"):
        open_read_only(garbage)
    other = tmp_path / "other.sqlite3"
    conn = sqlite3.connect(str(other))
    conn.execute("CREATE TABLE unrelated (x)")
    conn.commit()
    conn.close()
    with pytest.raises(StoreUnavailable, match="circuit_version"):
        open_read_only(other)


def test_capabilities_reflect_the_schema(ex):
    caps = ex.capabilities()
    assert caps.schema_version == 8 and caps.evaluations and caps.contributors and caps.technique_labels and caps.source_details


# == search ======================================================================================


def test_exact_version_id_key_and_label_searches(ex, ids):
    by_id = ex.search(ids["b"])
    assert [(m.version_id, kind) for m, kind in by_id.matches] == [(ids["b"], "version_id")]
    by_key = ex.search("DEMO:BBBB222")  # exact key, case-insensitive
    assert [(m.version_id, kind) for m, kind in by_key.matches] == [(ids["b"], "external_version_key")]
    assert ex.search("demo:aaaa111").matches[0][0].official.score == 100000 and ex.search("demo:aaaa111").matches[0][0].platform_status == "accepted"


def test_commit_fragment_contained_in_the_key_finds_the_version(ex, ids):
    result = ex.search("bbbb222")
    assert [(m.version_id, kind) for m, kind in result.matches] == [(ids["b"], "commit id / partial key")]
    assert matches(ex.search("ZERO0")) == ["demo:zero000"]


def test_a_short_identifier_matching_several_versions_returns_all_of_them_without_choosing(ex):
    result = ex.search("abc12")
    assert sorted(matches(result)) == ["demo:abc1234", "demo:abc1299"] and not result.truncated  # the UI must ask which one
    both = ex.search("V1")  # the label exists in two logical circuits
    assert len(both.matches) == 2 and {m.logical_circuit_name for m, _ in both.matches} == {"demo_add", "demo_other"}


def test_logical_circuit_filter_narrows_results(ex, ids):
    only_other = ex.search("V1", logical_circuit_id=ids["circuit2"])
    assert matches(only_other) == ["other:xyz9999"] and only_other.matches[0][0].version_label == "V1"
    assert ex.search("abc12", logical_circuit_id=ids["circuit2"]).matches == ()
    assert [c.name for c in ex.logical_circuits()] == ["demo_add", "demo_other"]


def test_short_queries_only_match_exactly_and_empty_queries_explain_themselves(ex):
    assert ex.search("ab").matches == () and "at least 3 characters" in ex.search("ab").message
    assert ex.search("").matches == () and "Enter a version id" in ex.search("   ").message


def test_like_wildcards_and_sql_metacharacters_are_data_not_syntax(ex):
    for hostile in ("%", "_", "demo:%", "demo:aaaa_11", "' OR 1=1 --", "x'; DROP TABLE circuit_version; --", "\\", '" OR ""="'):
        assert ex.search(hostile).matches == (), hostile
    assert ex.search("demo:aaaa111").matches  # and the table is still there


def test_search_results_are_limited_and_flagged_when_truncated(ex):
    result = ex.search("leaf", limit=10)
    assert len(result.matches) == 10 and result.truncated
    assert ex.search("leaf", limit=100).truncated is False and len(ex.search("leaf", limit=100).matches) == 30


# == neighbors ===================================================================================


def test_incoming_outgoing_and_both_directions(ex, ids):
    kinds = lambda edges: sorted((e.relation_type, e.edge_id) for e in edges)  # noqa: E731
    incoming = ex.edges_for(ids["b"], "in", None)
    assert kinds(incoming) == kinds([e for e in ex.edges_for(ids["b"], "both", None) if e.target_version_id == ids["b"]])
    outgoing = ex.edges_for(ids["b"], "out", None)
    assert [e.target_version_id for e in outgoing] == [ids["c"]]
    assert len(ex.edges_for(ids["b"], "both", None)) == len(incoming) + len(outgoing)
    with pytest.raises(ValueError):
        ex.edges_for(ids["b"], "sideways", None)


def test_relation_filters_and_the_empty_filter(ex, ids):
    only = ex.edges_for(ids["a"], "both", ("branched_from",))
    assert [e.edge_id for e in only] == [ids["e_ab_branch"]]
    assert ex.edges_for(ids["a"], "both", ()) == []
    assert {r.relation_type for r in ex.relation_types()} == {"next_promoted_commit", "branched_from", "historical_successor", "optimizes"}


def test_neighborhood_through_the_store_has_no_duplicate_nodes_or_edges_for_parallel_cyclic_and_self_links(ex, ids):
    state = ex.start(ids["a"])
    view = graph.project(state)
    assert sorted(n.external_version_key for n in view.nodes) == ["demo:aaaa111", "demo:bbbb222", "demo:cccc333"]
    assert len(view.edges) == len({e.edge_id for e in view.edges}) == 3 + 0  # a->b next, a->b branch, c->a
    assert {ids["e_ab_next"], ids["e_ab_branch"], ids["e_ca"]} == {e.edge_id for e in view.edges}  # the parallel pair is kept as two edges
    selfie = graph.project(ex.start(ids["d"]))
    assert len(selfie.nodes) == 1 and len(selfie.edges) == 1 and selfie.edges[0].edge_id == ids["e_dd"]


def test_labels_linked_to_an_edge_never_duplicate_graph_elements(ex, ids):
    view = graph.project(ex.start(ids["b"]))
    edge_ids = [e.edge_id for e in view.edges]
    assert len(edge_ids) == len(set(edge_ids))  # two labels point at a->b, yet the edge is drawn once
    assert len(ex.edge_detail(ids["e_ab_next"]).linked_labels) == 2


def test_the_node_limit_applies_to_a_high_degree_version(ex, ids):
    state = ex.start(ids["hub"], max_nodes=10)
    assert len(state.nodes) == 10 and graph.neighbors_hidden_by_limit(state) == 21
    assert len(graph.project(ex.start(ids["hub"], max_nodes=50), max_nodes=50).nodes) == 31  # the cap applies to loading AND to the projection


# == version details =============================================================================


def test_official_evaluation_and_timestamps_keep_their_source_strings_and_meanings(ex, ids):
    detail = ex.version_detail(ids["b"])
    ev = detail.evaluation
    assert (ev.platform_status, ev.promotion_status, ev.official.peak_qubits, ev.official.avg_executed_toffoli, ev.official.score) == ("accepted", "promoted", 110, 800, 88000)
    assert (ev.platform_created_at, ev.platform_updated_at, ev.promotion_finished_at) == ("2026-01-05T10:00:00.123Z", "2026-01-05T10:02:00.456Z", "2026-01-05T10:02:00.456Z")
    assert detail.summary.historical_time == "2026-01-02T00:00:00Z" and detail.discovered_at  # Git author date and QCH ingestion time are separate fields
    assert detail.submission_key == f"demo:{fx.UUID_B}" and detail.summary.submission_id == ids["sub_b"]


def test_rejected_and_failed_evaluations_show_stored_reasons_and_unavailable_metrics(ex, ids):
    rejected = ex.version_detail(ids["c"]).evaluation
    assert rejected.platform_status == "rejected" and rejected.rejection_reason == "score did not improve current best" and rejected.promotion_finished_at is None
    failed = ex.version_detail(ids["nomet"])
    assert failed.evaluation.platform_status == "failed" and not failed.evaluation.official.available and failed.summary.official.score is None


def test_a_version_without_a_submission_explains_what_is_missing(ex, ids):
    detail = ex.version_detail(ids["d"])
    assert detail.evaluation is None and detail.submitter is None and detail.source_details == () and not detail.summary.official.available
    assert "not linked to a submission" in detail.unavailable["submission"]
    assert ex.version_detail("no-such-version") is None


def test_submitter_provenance_and_declared_coauthors_are_separate_and_unresolved(ex, ids):
    detail = ex.version_detail(ids["b"])
    assert detail.submitter.handle == "alice" and detail.submitter.evidence_class == "AUTHORITATIVE" and detail.submitter.source_field == "solverAccountId"
    assert detail.submitter.handle_evidence_source == "demo_api:solverUsername" and detail.submitter.former_handles == ("old_alice",)
    assert [(c.declared_reference, c.evidence_class) for c in detail.declared_coauthors] == [("bob the free-text coauthor", "DECLARED")]
    assert not hasattr(detail.declared_coauthors[0], "contributor_identity_id")  # never bound to an identity


def test_author_technique_labels_carry_their_provenance_and_stay_apart_from_notes(ex, ids):
    labels = ex.version_detail(ids["b"]).technique_labels
    assert [(t.family, t.change_type, t.source_file_path, t.source_row_number) for t in labels] == [("Windowing", "K2 pair compressor", "appendix.tex", 1), ("Fusion", None, "second_census.tex", 2)]
    assert labels[0].source_repository_commit == "c" * 40 and labels[0].classification_status == "author_final_primary_label" and labels[0].incoming_edge_id == ids["e_ab_next"]
    a = ex.version_detail(ids["a"])
    assert a.technique_labels == () and "No author-assigned technique label" in a.unavailable["technique_labels"]  # a note is never turned into a label


def test_artifacts_are_reported_as_recorded_not_verified(ex, ids):
    (artifact,) = ex.version_detail(ids["b"]).artifacts
    assert artifact.recorded_status == "READY" and artifact.uri == "../somewhere/missing.kmx" and artifact.size_bytes == 12345  # the file does not exist; the reader did not check
    assert ex.version_detail(ids["a"]).artifacts == () and "artifact" in ex.version_detail(ids["a"]).unavailable["artifacts"]


# == submitter statements: latest vs older snapshots =============================================


def test_latest_source_details_come_from_the_newest_snapshot_that_has_the_submission(ex, ids):
    details = ex.version_detail(ids["b"]).source_details
    assert [d.is_latest for d in details] == [True, False]  # newest first
    latest, older = details
    assert latest.source_fetched_at == fx.T_FETCH_NEW and older.source_fetched_at == fx.T_FETCH_OLD
    assert latest.note == fx.NOTE_NEW and older.note == fx.NOTE_OLD  # shown independently, never merged
    assert (latest.claimed_score_json, older.claimed_score_json) == ("88000", "90000")
    assert (latest.acquisition, older.acquisition) == ("qch_fetch", "third_party_pinned_copy")
    assert latest.coauthors_presence == "value" and older.coauthors_presence == "absent"  # the older snapshot has no such key at all
    # a still newer snapshot that does not contain this submission (see the fixture) must not hide the older details
    assert len(details) == 2


def test_notes_keep_newlines_unicode_and_markup_exactly(ex, ids):
    note = ex.version_detail(ids["b"]).source_details[0].note
    assert note == fx.NOTE_NEW and "\r\n" in note and "ünïcødé ✓" in note and "<b>tags</b>" in note and "**markdown**" in note
    long = ex.version_detail(ids["c"]).source_details[0]
    assert long.note == fx.LONG_NOTE and long.note_chars == len(fx.LONG_NOTE) == 5200  # nothing is truncated on the way out of the database


def test_presence_states_absent_null_empty_value_survive_to_the_details(ex, ids):
    a = ex.version_detail(ids["a"]).source_details[0]
    assert (a.note_presence, a.note, a.claimed_score_presence, a.claimed_score_json, a.coauthors_presence, a.coauthors_json) == ("null", None, "null", None, "empty", "[]")
    c = ex.version_detail(ids["c"]).source_details[0]
    assert c.claimed_score_presence == "absent" and c.coauthors_presence == "absent"


def test_claimed_score_is_kept_apart_from_the_official_score(ex, ids):
    detail = ex.version_detail(ids["b"])
    assert detail.evaluation.official.score == 88000 and detail.source_details[1].claimed_score_json == "90000"  # an old claim that differs from the official value
    assert detail.summary.official.score == 88000


# == edge details ================================================================================


def test_edge_detail_official_deltas_and_percentage(ex, ids):
    detail = ex.edge_detail(ids["e_ab_next"])
    assert (detail.source.external_version_key, detail.target.external_version_key) == ("demo:aaaa111", "demo:bbbb222")
    assert (detail.official.q_delta, detail.official.t_delta, detail.official.s_delta) == (10, -200, -12000)
    assert detail.official.score_reduction_percent == pytest.approx(12.0)
    assert detail.target_detail.submitter.handle == "alice" and detail.target_detail.source_details[0].note == fx.NOTE_NEW


def test_edge_detail_stored_fields_and_evidence(ex, ids):
    edge = ex.edge_detail(ids["e_ab_branch"])
    assert edge.edge.description == "stored description" and edge.evidence == {"note": "kept verbatim", "n": 3} and edge.edge.relation_type == "branched_from"
    assert edge.linked_labels == ()  # the label belongs to the OTHER a->b edge; nothing is invented for this one
    assert ex.edge_detail("no-such-edge") is None


def test_edge_detail_linked_labels_use_incoming_edge_id_and_keep_all_provenance(ex, ids):
    labels = ex.edge_detail(ids["e_ab_next"]).linked_labels
    assert sorted(t.family for t in labels) == ["Fusion", "Windowing"] and {t.source_file_path for t in labels} == {"appendix.tex", "second_census.tex"}
    assert ex.edge_detail(ids["e_bc"]).linked_labels == ()


def test_edge_detail_zero_denominator_and_missing_metrics(ex, ids):
    zero = ex.edge_detail(ids["e_zb"])  # source score is exactly 0
    assert zero.official.s_delta == 88000 and zero.official.score_reduction_percent is None
    missing = ex.edge_detail(ids["e_nb"])  # source has a failed evaluation with no metrics
    assert missing.official is None and "not recorded for both endpoints" in missing.unavailable["official"]
    nosub = ex.edge_detail(ids["e_dd"])  # self-link on a version with no submission at all
    assert nosub.official is None and nosub.source.version_id == nosub.target.version_id


def test_edge_detail_unknown_relation_types_get_no_invented_meaning(ex, ids):
    from qch.explorer.service import RELATION_DESCRIPTIONS

    assert ex.edge_detail(ids["e_zb"]).edge.relation_type == "optimizes" and "optimizes" not in RELATION_DESCRIPTIONS
    assert {"next_promoted_commit", "branched_from", "historical_successor"} <= set(RELATION_DESCRIPTIONS)


# == missing optional tables =====================================================================


@pytest.mark.parametrize("dropped, section", [
    (["contribution", "contributor_alias", "contributor_import", "contributor_identity"], "submitter"),
    (["version_technique_label", "research_source"], "technique_labels"),
    (["submission_source_detail_latest", "submission_source_detail", "submission_detail_source"], "source_details"),
    (["artifact"], "artifacts"),
])
def test_missing_optional_tables_degrade_to_messages_and_core_exploration_still_works(tmp_path, dropped, section):
    path = tmp_path / "trimmed.sqlite3"
    ids = fx.build_explorer_db(path)
    fx.drop_optional_tables(path, dropped)
    with EvolutionExplorer(open_read_only(path)) as explorer:
        assert explorer.search("bbbb222").matches  # search and graph are unaffected
        assert len(graph.project(explorer.start(ids["b"])).nodes) == 5  # b plus a, c, zero and nomet
        detail = explorer.version_detail(ids["b"])
        assert detail.unavailable[section]
        assert detail.evaluation is not None and detail.summary.official.score == 88000  # the rest of the details is still there
        assert explorer.edge_detail(ids["e_ab_next"]).official.s_delta == -12000


def test_a_store_without_evaluation_tables_still_searches_and_draws_the_graph(tmp_path):
    path = tmp_path / "noeval.sqlite3"
    ids = fx.build_explorer_db(path, with_optional=False)
    fx.drop_optional_tables(path, ["submission_evaluation", "evaluation_snapshot"])
    with EvolutionExplorer(open_read_only(path)) as explorer:
        result = explorer.search("bbbb222")
        assert result.matches and not result.matches[0][0].official.available and result.matches[0][0].platform_status is None
        view = graph.project(explorer.start(ids["b"]))
        assert len(view.nodes) == 5 and len(view.edges) == 5
        detail = explorer.version_detail(ids["b"])
        assert detail.evaluation is None and "no evaluation tables" in detail.unavailable["evaluation"]
        assert explorer.edge_detail(ids["e_ab_next"]).official is None


def test_a_bare_core_schema_is_enough(tmp_path):
    path = tmp_path / "core.sqlite3"
    ids = fx.build_explorer_db(path, with_optional=False)
    fx.drop_optional_tables(path, ["submission_evaluation", "evaluation_snapshot", "submission_source_detail_latest", "submission_source_detail",
                                   "submission_detail_source", "structural_metric", "benchmark_run", "artifact", "source_commit", "version_source",
                                   "submission_source_commit", "contribution", "contributor_alias", "contributor_import", "contributor_identity",
                                   "version_technique_label", "research_source", "logical_circuit"])
    with EvolutionExplorer(open_read_only(path)) as explorer:
        assert explorer.search("demo:aaaa111").matches and len(graph.project(explorer.start(ids["a"])).nodes) == 3
        detail = explorer.version_detail(ids["a"])
        assert detail.summary.display_key == "demo:aaaa111" and detail.git_commits == () and detail.structural_metrics == ()
        assert explorer.edge_detail(ids["e_ab_next"]) is not None
