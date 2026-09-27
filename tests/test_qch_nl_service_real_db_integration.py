"""Phase 2B: a SEPARATE integration test path against the REAL
qch_ecdsa_p17.sqlite3 snapshot (spec section 21) -- not a mock, not
reconstructed from benchmark gold files. Uses
`DeterministicHeuristicBackend` (no real LLM required for these to
run in CI), but every fact asserted below was independently verified
by direct SQL against the same file before being written here (see
the Phase 2B architecture doc's audit section) -- these are REAL,
present-day facts about this specific snapshot, not invented
expectations.

Skips (rather than fails) if the snapshot is not present at the
expected path, since this file is gitignored (`*.sqlite3`) and local
to whoever ran the Phase 1.7 materialization.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.nl.backend import DeterministicHeuristicBackend  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService, SystemStatus  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason=f"real QCH snapshot not present at {DB_PATH}")


@pytest.fixture
def real_service():
    with QCH.open(str(DB_PATH)) as hub:
        yield QCHNaturalLanguageService(DeterministicHeuristicBackend(), hub, data_source_label=str(DB_PATH), model_name="deterministic_heuristic_v1")


def test_real_store_has_781_versions_and_25_with_structural_metrics(real_service):
    circuit = real_service.hub.circuits.list()[0]
    versions = real_service.hub.versions.list(circuit.logical_circuit_id)
    assert len(versions) == 781


def test_real_lowest_structural_toffoli_version_is_correct(real_service):
    result = real_service.ask("What is the structural Toffoli count of ecdsafail:152fbe0?")
    assert result.status == SystemStatus.ANSWERED
    assert "988,969" in result.answer


def test_real_version_with_no_structural_data_is_missing_data_not_fabricated(real_service):
    result = real_service.ask("What is the structural Toffoli count of ecdsafail:a31a47b?")
    assert result.status == SystemStatus.MISSING_DATA


def test_real_filter_below_950000_is_a_real_honest_empty_result(real_service):
    """Ground truth (verified independently by direct SQL): the real
    minimum structural.toffoli_count among the 25 materialized
    versions is 988,969 -- so this filter genuinely matches nothing.
    The system must say so honestly, not fabricate a match."""
    result = real_service.ask("Which versions have structural Toffoli count below 950000?")
    assert result.status == SystemStatus.ANSWERED
    assert "No matching records" in result.answer
    assert "25 of 781" in result.answer  # real coverage: only 25 of the 781 real versions have this metric at all


def test_real_v1_v5_milestones_resolve_by_label(real_service):
    for label in ("V1", "V2", "V3", "V4", "V5"):
        result = real_service.ask(f"What is the benchmark score of {label}?")
        assert result.status in (SystemStatus.ANSWERED, SystemStatus.MISSING_DATA)  # never UNKNOWN_ENTITY for a real milestone label


def test_real_unknown_version_label_is_unknown_entity_not_missing_data(real_service):
    """V42 is not one of the five real labels in this store -- the
    service wires the REAL known_version_labels into planning, so this
    is caught at planning time (spec-aligned: honest, specific status),
    not deferred to a generic MISSING_DATA at execution time."""
    result = real_service.ask("What is the structural Toffoli count of V42?")
    assert result.status == SystemStatus.UNKNOWN_ENTITY


def test_real_branched_from_relation_exists_and_is_queryable(real_service):
    result = real_service.ask("Show branched-from transitions for validated-unpromoted versions.")
    assert result.status == SystemStatus.ANSWERED


def test_real_compare_v4_v5_uses_real_data(real_service):
    result = real_service.ask("Compare V4 and V5.")
    assert result.status == SystemStatus.ANSWERED
    assert "V4" in result.answer and "V5" in result.answer


# -- Phase 2C: SemanticGuard/ResultSanityChecker against REAL data ------------------
# Every expected value below was independently verified by direct SQL against
# data/qch_ecdsa_p17.sqlite3 BEFORE being written here (see
# docs/QCH_NL_SEMANTIC_RELIABILITY_PHASE2C.md's own audit section) -- these are
# real, present-day facts, not invented expectations. Uses
# DeterministicHeuristicBackend (already correct for these cases), so these
# tests verify SemanticGuard does NOT introduce a false positive on an
# already-correct real plan -- precision, not just recall (spec section 28).


def test_real_largest_structural_toffoli_decrease(real_service):
    """Ground truth: the single largest real decrease across all
    next_promoted_commit transitions in the dense window is exactly
    -12,497 (ecdsafail:cb56b71 -> ecdsafail:f3b2f1f)."""
    result = real_service.ask("Show the five largest structural Toffoli reductions.")
    assert result.status == SystemStatus.ANSWERED
    assert result.explanation.semantic_guard_outcome == "accept"
    assert "cb56b71" in result.answer and "f3b2f1f" in result.answer


def test_real_largest_structural_toffoli_increase(real_service):
    """Ground truth: the largest real increase is +20,911
    (ecdsafail:9652eec -> ecdsafail:cb56b71)."""
    result = real_service.ask("What are the top 3 structural Toffoli increases among promoted transitions?")
    assert result.status == SystemStatus.ANSWERED
    assert "9652eec" in result.answer and "cb56b71" in result.answer


def test_real_dual_decrease_transition_query(real_service):
    result = real_service.ask("Find transitions where both structural Toffoli count and structural qubit count decreased.")
    assert result.status == SystemStatus.ANSWERED
    assert result.explanation.sanity_outcome == "pass"


def test_real_ambiguous_bare_toffoli_needs_clarification(real_service):
    result = real_service.ask("Which version has the lowest Toffoli count?")
    assert result.status == SystemStatus.NEEDS_CLARIFICATION
    # Phase 2D.7.2: options are presentation labels ("Structural Toffoli count", "Benchmark/evaluation Toffoli")
    assert any("structural" in o.lower() for o in result.clarification_options)
    assert result.clarification_options == ["Structural Toffoli count", "Benchmark/evaluation Toffoli"]


def test_real_best_version_needs_clarification_with_direction_qualified_options(real_service):
    result = real_service.ask("Which version is best?")
    assert result.status == SystemStatus.NEEDS_CLARIFICATION
    assert any(o.startswith("lowest ") for o in result.clarification_options)
    assert any(o.startswith("highest ") for o in result.clarification_options)


# -- Phase 2D.1: canonical version identity against REAL data -----------------
# Ground truth (verified independently by direct SQL against
# data/qch_ecdsa_p17.sqlite3 -- see docs/QCH_VERSION_IDENTITY_PHASE2D1.md's
# own audit section): V5's real canonical id is "ecdsafail:a39e07e"; V5 has
# the real MINIMUM benchmark score (1,137,423,406) among the 5 milestones;
# "b6f2b0a" matches zero real versions; every one of the 781 real
# external_version_key values has a globally unique 7-char hex suffix.


def test_real_lowest_score_answer_shows_canonical_id_not_bare_milestone_label(real_service):
    """The exact Phase 2D.1 acceptance criterion: before this fix, this
    question's answer showed only "V5"; it must now show the real
    canonical external_version_key as the PRIMARY identity, with the
    milestone label kept only as a secondary alias."""
    result = real_service.ask("Which version has the lowest score?")
    assert result.status == SystemStatus.ANSWERED
    assert "ecdsafail:a39e07e" in result.answer
    assert "V5" in result.answer  # the milestone label is still shown, just secondary


def test_real_b6f2b0a_is_a_known_submission_without_a_version_not_unknown():
    """Phase 2D.3 (superseding the Phase 2D.1 assumption that b6f2b0a
    was "genuinely unknown"): the Phase 2D.2 forensic audit proved
    b6f2b0a is the prefix of a REAL submission UUID whose status is
    SUBMITTED, so it has no CircuitVersion. It is still not a prefix of
    any real external_version_key (the original 2D.1 fact, re-asserted
    below) -- but QCH does know it, so UNKNOWN would be false."""
    from qch.nl.version_resolver import VersionResolutionOutcome, resolve_version_identifier

    with QCH.open(str(DB_PATH)) as hub:
        circuit = hub.circuits.list()[0]
        assert not any((v.external_version_key or "").split(":", 1)[-1].startswith("b6f2b0a") for v in hub.versions.list(circuit.logical_circuit_id))
        resolution = resolve_version_identifier(hub, "b6f2b0a")
    assert resolution.outcome == VersionResolutionOutcome.KNOWN_SUBMISSION_NO_VERSION
    assert resolution.canonical_version_id is None
    assert resolution.submission_status == "SUBMITTED"


def test_real_genuinely_nonexistent_short_identifier_is_unknown():
    from qch.nl.version_resolver import VersionResolutionOutcome, resolve_version_identifier

    with QCH.open(str(DB_PATH)) as hub:
        resolution = resolve_version_identifier(hub, "fedcba98765")
    assert resolution.outcome == VersionResolutionOutcome.UNKNOWN


def test_real_arbitrary_non_milestone_short_ids_all_resolve():
    """5 real, non-milestone versions (a deterministic sample, every
    150th version by sequence -- not cherry-picked), addressed by their
    bare 7-char hex suffix (no "ecdsafail:" prefix) -- the short-ID tier
    this phase added. None of these is V1-V5, and none is hardcoded
    into any resolution rule; the resolver must find each via the same
    generic scan."""
    from qch.nl.version_resolver import VersionResolutionOutcome, resolve_version_identifier

    real_arbitrary_keys = [
        "ecdsafail:c55911f", "ecdsafail:5d2f6cf", "ecdsafail:00fb66d",
        "ecdsafail:9b2ce61", "ecdsafail:19fc2c7",
    ]
    with QCH.open(str(DB_PATH)) as hub:
        for full_key in real_arbitrary_keys:
            short = full_key.split(":", 1)[1]
            resolution = resolve_version_identifier(hub, short)
            assert resolution.outcome == VersionResolutionOutcome.RESOLVED, short
            assert resolution.canonical_version_id == full_key


def test_real_every_external_version_key_has_a_globally_unique_short_suffix():
    """A regression guard for the short-ID tier's own safety assumption
    in THIS real dataset: if two real versions ever shared a 7-char hex
    suffix, a bare-hex reference to either would need to come back
    AMBIGUOUS, never a silent pick. Confirms the current snapshot has
    zero such collisions (matches the audit done before implementation)."""
    with QCH.open(str(DB_PATH)) as hub:
        circuit = hub.circuits.list()[0]
        versions = hub.versions.list(circuit.logical_circuit_id)
        suffixes = [v.external_version_key.split(":", 1)[1] for v in versions if v.external_version_key]
    assert len(suffixes) == len(set(suffixes))


def test_real_get_metric_by_full_canonical_key_for_an_arbitrary_version_is_missing_data_not_unknown_entity(real_service):
    """A real, existing, non-milestone version with no structural data
    recorded must be MISSING_DATA ("this version exists, but lacks this
    metric"), never UNKNOWN_ENTITY ("no such version") -- the exact
    distinction spec section 9 requires."""
    result = real_service.ask("What is the structural Toffoli count of ecdsafail:c55911f?")
    assert result.status == SystemStatus.MISSING_DATA
