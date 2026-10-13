"""QCH schema v6/v7: ECDSA.Fail research-supplement import (Supplemental Note C
technique labels + the paper's Pareto candidate set and its published Table 2
frontier as two separate populations).

Every write test runs on a TEMPORARY COPY of the real store -- never on
data/qch_ecdsa_p17.sqlite3 itself. Tests needing the pinned staging files
skip when data/external/... is absent (it is git-ignored).
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.exceptions import ValidationError  # noqa: E402
from qch.importers.ecdsafail_research_supplement import (  # noqa: E402
    CANDIDATE_POPULATION_NAME,
    CURATION_PATH,
    EXPECTED_NOTE_C_FAMILIES,
    FRONTIER_POPULATION_NAME,
    LEGACY_POPULATION_NAME,
    NOTE_C_PATH,
    PARETO_MANIFEST_PATH,
    RAW_RESPONSE_PATH,
    ResearchImportBlocked,
    apply_research_supplement_import,
    load_pinned_sources,
    pareto_frontier,
    parse_note_c,
    plan_paper_populations,
    plan_technique_labels,
)
from qch.models import PopulationMembership, TechniqueLabel  # noqa: E402

REAL_DB = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
MANIFEST = PROJECT_ROOT / "data" / "external" / "ecdsafail" / "research_supplement" / "SOURCE_MANIFEST.json"
EXISTING_TABLES = [
    "artifact", "benchmark_run", "circuit_version", "contribution", "contributor_alias", "contributor_identity", "contributor_import",
    "evaluation_snapshot", "ingestion_job", "logical_circuit", "materialization_job", "source_commit", "structural_metric", "submission",
    "submission_evaluation", "submission_source_commit", "transformation_edge", "verification_result", "version_source", "version_tag",
]
NEW_TABLES = ["research_source", "version_technique_label", "research_population", "population_membership"]
PAPER_EIGHT = ["b6f2b0a", "f1d8707", "39a9b5f", "a3b0148", "4352cfb", "1c0e0e9", "8e9c9a2", "a536a48"]  # Table 2, Q ascending
CURATED_SIX = {"b6f2b0a", "f1d8707", "39a9b5f", "a3b0148", "4352cfb", "1c0e0e9"}
IMPORTED_COUNTS = {"research_source": 2, "version_technique_label": 404, "research_population": 2, "population_membership": 414 + 8}

needs_sources = pytest.mark.skipif(not (REAL_DB.exists() and MANIFEST.exists()), reason="real store or pinned staging files not present")


@pytest.fixture(scope="module")
def pinned():
    if not MANIFEST.exists():
        pytest.skip("pinned staging files not present")
    return load_pinned_sources(MANIFEST, PROJECT_ROOT)


@pytest.fixture
def copy_hub(tmp_path):
    if not REAL_DB.exists():
        pytest.skip("real store not present")
    db = tmp_path / "store.sqlite3"
    shutil.copy2(REAL_DB, db)
    hub = QCH.open(str(db))
    # The production store may already contain this import: start every test from
    # the pre-import state by clearing the four v6 tables ON THIS TEMPORARY COPY only.
    conn = hub._storage._conn
    for table in ("population_membership", "research_population", "version_technique_label", "research_source"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    yield hub
    hub.close()


def _counts(hub, tables):
    conn = hub._storage._conn
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}


def _plans(hub, pinned):
    return plan_technique_labels(hub, pinned[NOTE_C_PATH]), plan_paper_populations(
        hub, pinned[CURATION_PATH], pinned[PARETO_MANIFEST_PATH], pinned[RAW_RESPONSE_PATH])


def _population_rows(hub):
    conn = hub._storage._conn
    return (conn.execute("SELECT * FROM research_population ORDER BY population_id").fetchall(),
            conn.execute("SELECT * FROM population_membership ORDER BY population_id, submission_id").fetchall())


def _make_v6_state(hub, pinned):
    """Reproduces the schema-v6 import on the copy: the eight Table 2 points
    stored under the former name with the candidate set's rule text."""
    technique, population = _plans(hub, pinned)
    hub.research.record_import(technique.source, labels=technique.labels)
    frontier, members = population.entries[-1]
    legacy = dataclasses.replace(frontier, name=LEGACY_POPULATION_NAME, semantics="v6 semantics", rule_text=population.entries[0][0].rule_text,
                                 population_role=None, derived_from_population_id=None, rule_source_field=None, provenance={})
    hub.research.record_import(population.source, population=legacy, memberships=members)
    return population


# == pure parsing / computation ===================================================================


@needs_sources
def test_note_c_parses_404_unique_commits_with_the_audited_family_counts(pinned):
    rows = parse_note_c(pinned[NOTE_C_PATH].data.decode("utf-8"))
    assert len(rows) == 404 and len({r.commit for r in rows}) == 404
    fams = {}
    for r in rows:
        fams[r.family] = fams.get(r.family, 0) + 1
    assert fams == EXPECTED_NOTE_C_FAMILIES
    unscored = [r for r in rows if r.score is None]
    assert {r.commit for r in unscored} == {"15a06af", "971d801", "c55911f", "ca1bf33"}
    assert all(r.delta is None and r.change_type is None for r in unscored)  # never manufactured


def test_hash_mismatch_blocks_before_anything_is_read(tmp_path):
    if not MANIFEST.exists():
        pytest.skip("pinned staging files not present")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for entry in manifest["sources"]:
        if entry["local_path"].endswith(NOTE_C_PATH):
            entry["sha256"] = "0" * 64
    bad = tmp_path / "manifest.json"
    bad.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ResearchImportBlocked, match="sha256 mismatch"):
        load_pinned_sources(bad, PROJECT_ROOT)


def test_pareto_frontier_dominance_keeps_ties_and_drops_weakly_dominated_points():
    points = [("a", 1, 9), ("b", 2, 5), ("c", 2, 5), ("d", 2, 6), ("e", 3, 5), ("f", 4, 1)]
    # d: dominated by b (same Q, larger T); e: dominated by b (larger Q, same T); b/c identical -> both kept
    assert pareto_frontier(points) == ["a", "b", "c", "f"]


# == import on a temporary copy ===================================================================


@needs_sources
def test_import_on_copy_counts_joins_and_existing_tables_unchanged(copy_hub, pinned):
    before = _counts(copy_hub, EXISTING_TABLES)
    technique, population = _plans(copy_hub, pinned)
    assert technique.blocking == [] and population.blocking == []
    assert technique.stats["labelled_next_promoted_commit_edges"] == 403
    assert (technique.stats["delta_pairs"], technique.stats["delta_agree"]) == (399, 399) and technique.delta_mismatches == []
    result = apply_research_supplement_import(copy_hub, technique, population)
    assert result.technique_labels_written and result.population_written
    assert _counts(copy_hub, EXISTING_TABLES) == before
    assert _counts(copy_hub, NEW_TABLES) == IMPORTED_COUNTS

    labels = copy_hub.research.list_technique_labels()
    assert sum(1 for label in labels if label.incoming_edge_id) == 403
    assert sum(1 for label in labels if not label.source_scored) == 4 and all(
        label.source_score is None and label.source_frontier_delta is None for label in labels if not label.source_scored)
    members = copy_hub.research.list_members(FRONTIER_POPULATION_NAME)
    assert sorted(m.source_ref for m in members) == sorted(PAPER_EIGHT)
    versions_by_submission = {v.realized_from_submission_id for c in copy_hub.circuits.list() for v in copy_hub.versions.list(c.logical_circuit_id)}
    assert sum(1 for m in members if m.submission_id not in versions_by_submission) == 6  # submission-scoped: six have no CircuitVersion
    pop = copy_hub.research.get_population(FRONTIER_POPULATION_NAME)
    source = copy_hub.research.get_source(pop.source_id)
    assert source.file_path == CURATION_PATH and source.sha256 == pinned[CURATION_PATH].sha256  # traceable to the pinned curation file
    assert "Table 2" in pop.semantics and "not an admission rule" in pop.semantics
    assert copy_hub.research.get_population(LEGACY_POPULATION_NAME) is None  # the former name is never reused

    conn = copy_hub._storage._conn
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


@needs_sources
def test_published_frontier_is_not_mistaken_for_the_candidate_set(copy_hub, pinned):
    apply_research_supplement_import(copy_hub, *_plans(copy_hub, pinned))
    candidate = copy_hub.research.get_population(CANDIDATE_POPULATION_NAME)
    frontier = copy_hub.research.get_population(FRONTIER_POPULATION_NAME)
    assert candidate.population_id != frontier.population_id
    assert (candidate.population_role, frontier.population_role) == ("candidate_set", "published_pareto_frontier")
    assert frontier.derived_from_population_id == candidate.population_id and candidate.derived_from_population_id is None
    cand_ids = {m.submission_id for m in copy_hub.research.list_members(CANDIDATE_POPULATION_NAME)}
    front_ids = {m.submission_id for m in copy_hub.research.list_members(FRONTIER_POPULATION_NAME)}
    assert front_ids < cand_ids and (len(cand_ids), len(front_ids)) == (414, 8)
    # the candidate count equals an independent derivation from the pinned response
    rows = json.loads(pinned[RAW_RESPONSE_PATH].data)["submissions"]
    cutoff = candidate.cutoff_at
    promoted = {r["id"] for r in rows if r.get("promotionStatus") == "promoted" and r["createdAt"] <= cutoff}  # same ISO-ms-Z format
    assert len(cand_ids) == len(promoted) + len(CURATED_SIX)


@needs_sources
def test_no_population_rule_claims_all_promoted_while_holding_only_the_frontier(copy_hub, pinned):
    apply_research_supplement_import(copy_hub, *_plans(copy_hub, pinned))
    for pop in copy_hub.research.list_populations():
        n = len(copy_hub.research.list_members(pop.name))
        if "all promoted" in pop.rule_text.lower():
            assert pop.population_role == "candidate_set" and n == 414, (pop.name, n)
        if pop.population_role == "published_pareto_frontier":
            assert "all promoted" not in pop.rule_text.lower() and pop.rule_source_field is None and n == 8
    frontier = copy_hub.research.get_population(FRONTIER_POPULATION_NAME)
    kept = frontier.provenance["candidate_set_rule_verbatim"]  # verbatim source text kept, labelled with what it applies to
    assert kept["applies_to"] == CANDIDATE_POPULATION_NAME and kept["text"] == copy_hub.research.get_population(CANDIDATE_POPULATION_NAME).rule_text


@needs_sources
def test_no_rejected_submission_outside_the_curated_six_enters_the_candidate_set(copy_hub, pinned):
    apply_research_supplement_import(copy_hub, *_plans(copy_hub, pinned))
    evals = copy_hub.evaluations.latest_all()
    members = copy_hub.research.list_members(CANDIDATE_POPULATION_NAME)
    not_promoted = {m.source_ref for m in members if evals[m.submission_id].promotion_status != "promoted"}
    assert not_promoted == CURATED_SIX
    assert all(evals[m.submission_id].platform_created_at <= "2026-07-26T09:21:55.494Z" for m in members)
    assert {m.source_ref for m in members if m.member_role == "curated_rejected"} == CURATED_SIX


@needs_sources
def test_a_rejected_row_posing_as_candidate_blocks_the_plan(copy_hub, pinned):
    evals = copy_hub.evaluations.latest_all()
    rows = json.loads(pinned[RAW_RESPONSE_PATH].data)["submissions"]
    in_qch = {e.source_submission_uuid for e in evals.values()}
    intruder = next(r for r in rows if r["status"] == "rejected" and r["id"][:7] not in CURATED_SIX and r["id"] in in_qch
                    and (r.get("officialMetrics") or {}).get("qubits") and r["createdAt"] <= "2026-07-26T09:21:55.494Z")
    intruder["promotionStatus"] = "promoted"
    tampered = dataclasses.replace(pinned[RAW_RESPONSE_PATH], data=json.dumps({"submissions": rows}).encode("utf-8"))
    plan = plan_paper_populations(copy_hub, pinned[CURATION_PATH], pinned[PARETO_MANIFEST_PATH], tampered)
    assert any(intruder["id"][:7] in issue and "not one of the six curated rejected IDs" in issue for issue in plan.blocking)
    with pytest.raises(ResearchImportBlocked):
        apply_research_supplement_import(copy_hub, plan_technique_labels(copy_hub, pinned[NOTE_C_PATH]), plan)
    assert _counts(copy_hub, NEW_TABLES) == {t: 0 for t in NEW_TABLES}


@needs_sources
def test_computed_frontier_equals_the_eight_pinned_ids(copy_hub, pinned):
    _, population = _plans(copy_hub, pinned)
    assert population.stats["computed_frontier_ids"] == PAPER_EIGHT and population.stats["computed_equals_listed"]
    evals = copy_hub.evaluations.latest_all()
    points = [(m.source_ref, evals[m.submission_id].official_peak_qubits, evals[m.submission_id].official_avg_executed_toffoli)
              for m in population.entries[0][1]]
    assert len(points) == 414 and pareto_frontier(points) == PAPER_EIGHT  # recomputed here from QCH metrics
    # a listed ID the computation does not reproduce must block
    curation = json.loads(pinned[CURATION_PATH].data)
    curation["admission_policy"]["paper_admitted_pareto_ids_q_ascending"][-1] = "4c5a972"
    bad = dataclasses.replace(pinned[CURATION_PATH], data=json.dumps(curation).encode("utf-8"))
    blocked = plan_paper_populations(copy_hub, bad, pinned[PARETO_MANIFEST_PATH], pinned[RAW_RESPONSE_PATH])
    assert any("computed Pareto frontier" in issue for issue in blocked.blocking)


@needs_sources
def test_correction_of_the_v6_row_keeps_members_and_repeats_as_a_no_op(copy_hub, pinned):
    population = _make_v6_state(copy_hub, pinned)
    v6_members = copy_hub._storage._conn.execute(
        "SELECT * FROM population_membership WHERE population_id = ? ORDER BY submission_id", (population.population.population_id,)).fetchall()
    assert copy_hub.research.get_population(LEGACY_POPULATION_NAME) is not None
    first = apply_research_supplement_import(copy_hub, *_plans(copy_hub, pinned))
    assert (first.technique_labels_written, first.population_written) == (False, True)
    assert copy_hub.research.get_population(LEGACY_POPULATION_NAME) is None
    frontier = copy_hub.research.get_population(FRONTIER_POPULATION_NAME)
    assert frontier.population_id == population.population.population_id and frontier.provenance["former_names"] == [LEGACY_POPULATION_NAME]
    assert copy_hub._storage._conn.execute(
        "SELECT * FROM population_membership WHERE population_id = ? ORDER BY submission_id", (frontier.population_id,)).fetchall() == v6_members
    snapshot = (_population_rows(copy_hub), [dataclasses.astuple(label) for label in copy_hub.research.list_technique_labels()])
    again = apply_research_supplement_import(copy_hub, *_plans(copy_hub, pinned))
    assert (again.technique_labels_written, again.population_written) == (False, False)
    assert (_population_rows(copy_hub), [dataclasses.astuple(label) for label in copy_hub.research.list_technique_labels()]) == snapshot
    assert _counts(copy_hub, NEW_TABLES) == IMPORTED_COUNTS


@needs_sources
def test_correction_failing_midway_leaves_no_partial_membership(copy_hub, pinned):
    _make_v6_state(copy_hub, pinned)
    before = _population_rows(copy_hub)
    _, population = _plans(copy_hub, pinned)
    candidate, members = population.entries[0]
    bogus = PopulationMembership(candidate.population_id, "no-such-submission", "fffffff", "test", "promoted")
    entries = [(candidate, [*members, bogus]), population.entries[1]]  # FK failure after 414 candidate rows were inserted
    with pytest.raises(ValidationError):
        copy_hub.research.record_populations(population.source, entries, population.renames)
    assert _population_rows(copy_hub) == before  # no candidate rows, v6 row not renamed
    assert copy_hub.research.get_population(CANDIDATE_POPULATION_NAME) is None


@needs_sources
def test_correction_refuses_to_rewrite_a_different_member_set(copy_hub, pinned):
    _make_v6_state(copy_hub, pinned)
    before = _population_rows(copy_hub)
    _, population = _plans(copy_hub, pinned)
    frontier, members = population.entries[1]
    with pytest.raises(ValidationError, match="member set differs"):
        copy_hub.research.record_populations(population.source, [population.entries[0], (frontier, members[:-1])], population.renames)
    assert _population_rows(copy_hub) == before


@needs_sources
def test_conflicting_reimport_fails_explicitly_and_changes_nothing(copy_hub, pinned):
    technique, population = _plans(copy_hub, pinned)
    apply_research_supplement_import(copy_hub, technique, population)
    stored = [dataclasses.astuple(label) for label in copy_hub.research.list_technique_labels()]
    altered = dataclasses.replace(technique, labels=[dataclasses.replace(technique.labels[0], family="Something Else"), *technique.labels[1:]])
    with pytest.raises(ValidationError, match="conflicts with the stored import"):
        copy_hub.research.record_import(altered.source, labels=altered.labels)
    assert [dataclasses.astuple(label) for label in copy_hub.research.list_technique_labels()] == stored


@needs_sources
def test_invalid_mapping_blocks_the_whole_import(copy_hub, pinned):
    tampered = dataclasses.replace(pinned[NOTE_C_PATH], data=pinned[NOTE_C_PATH].data.replace(b"\\texttt{75851e6}", b"\\texttt{fffffff}", 1))
    technique = plan_technique_labels(copy_hub, tampered)
    population = plan_paper_populations(copy_hub, pinned[CURATION_PATH], pinned[PARETO_MANIFEST_PATH], pinned[RAW_RESPONSE_PATH])
    assert any("fffffff" in issue for issue in technique.blocking)
    with pytest.raises(ResearchImportBlocked):
        apply_research_supplement_import(copy_hub, technique, population)
    assert _counts(copy_hub, NEW_TABLES) == {t: 0 for t in NEW_TABLES}  # nothing from EITHER source was written


@needs_sources
def test_failure_inside_the_transaction_rolls_back_every_row(copy_hub, pinned):
    technique, _ = _plans(copy_hub, pinned)
    bogus = TechniqueLabel(**{**dataclasses.asdict(technique.labels[-1]), "label_id": "bogus", "version_id": "no-such-version", "source_row_number": 9999})
    with pytest.raises(ValidationError):  # FOREIGN KEY failure on the LAST insert, after the source row and 404 labels
        copy_hub.research.record_import(technique.source, labels=[*technique.labels, bogus])
    assert _counts(copy_hub, NEW_TABLES) == {t: 0 for t in NEW_TABLES}
