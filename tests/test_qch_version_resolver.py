"""Phase 2D.1: deterministic unit tests for `qch.nl.version_resolver` --
no LLM, no real snapshot required (see test_qch_nl_service_real_db_integration.py
for the separate real-database tests). Uses a small, REAL in-memory
`QCH` hub, matching this project's existing convention (see
tests/test_qch_nl_service.py's own `real_hub` fixture) -- never a mock
of `hub.versions`/`hub.circuits`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.nl.version_resolver import (  # noqa: E402
    PlanCanonicalizationOutcome,
    VersionResolutionOutcome,
    canonicalize_plan_versions,
    extract_candidate_version_identifiers,
    resolve_version_identifier,
)
from qch.query.models import QCHQueryPlan, QCHQueryStep  # noqa: E402


@pytest.fixture
def hub():
    h = QCH.open(":memory:")
    circuit = h.circuits.create("qch:logical:test_circuit", name="test_circuit")
    h.versions.create(circuit.logical_circuit_id, external_version_key="ecdsafail:aaa0001", version_label="V1", sequence_no=1)
    h.versions.create(circuit.logical_circuit_id, external_version_key="ecdsafail:aaa0002", version_label="V2", sequence_no=2)
    # no version_label -- most real versions have none (see known_limitations)
    h.versions.create(circuit.logical_circuit_id, external_version_key="ecdsafail:ccc9999", sequence_no=3)
    # a deliberate short-ID collision pair for the AMBIGUOUS tier
    h.versions.create(circuit.logical_circuit_id, external_version_key="ecdsafail:bbbb234", sequence_no=4)
    h.versions.create(circuit.logical_circuit_id, external_version_key="ecdsafail:bbbb678", sequence_no=5)
    yield h
    h.close()


# -- A. exact match tiers ------------------------------------------------------


def test_a_exact_full_canonical_key_resolves(hub):
    r = resolve_version_identifier(hub, "ecdsafail:aaa0001")
    assert r.outcome == VersionResolutionOutcome.RESOLVED
    assert r.canonical_version_id == "ecdsafail:aaa0001"
    assert r.version_label == "V1"
    assert r.resolution_status == "exact_match"


def test_b_exact_internal_version_id_resolves(hub):
    version = hub.versions.find_by_external_key("ecdsafail:aaa0002")
    r = resolve_version_identifier(hub, version.version_id)
    assert r.outcome == VersionResolutionOutcome.RESOLVED
    assert r.canonical_version_id == "ecdsafail:aaa0002"


def test_c_exact_milestone_label_resolves_to_canonical_key(hub):
    r = resolve_version_identifier(hub, "V1")
    assert r.outcome == VersionResolutionOutcome.RESOLVED
    assert r.canonical_version_id == "ecdsafail:aaa0001"
    assert r.version_label == "V1"


def test_d_case_insensitive_exact_match(hub):
    r = resolve_version_identifier(hub, "ECDSAFAIL:AAA0001")
    assert r.outcome == VersionResolutionOutcome.RESOLVED
    assert r.canonical_version_id == "ecdsafail:aaa0001"
    assert r.resolution_status == "exact_match_case_normalized"


# -- E/F. short-ID (DB-grounded, not string-prepending) ------------------------


def test_e_bare_hex_without_prefix_resolves_via_short_id_tier(hub):
    r = resolve_version_identifier(hub, "aaa0001")
    assert r.outcome == VersionResolutionOutcome.RESOLVED
    assert r.canonical_version_id == "ecdsafail:aaa0001"
    assert r.resolution_status == "short_id_match"


def test_f_unique_short_prefix_resolves(hub):
    r = resolve_version_identifier(hub, "ccc9")
    assert r.outcome == VersionResolutionOutcome.RESOLVED
    assert r.canonical_version_id == "ecdsafail:ccc9999"


def test_g_colliding_short_prefix_is_ambiguous_not_guessed(hub):
    r = resolve_version_identifier(hub, "bbbb")
    assert r.outcome == VersionResolutionOutcome.AMBIGUOUS
    assert r.canonical_version_id is None
    assert set(r.candidates) == {"ecdsafail:bbbb234", "ecdsafail:bbbb678"}


# -- H. genuine non-existence, never guessed -----------------------------------


def test_h_nonexistent_identifier_is_unknown(hub):
    r = resolve_version_identifier(hub, "b6f2b0a")
    assert r.outcome == VersionResolutionOutcome.UNKNOWN
    assert r.canonical_version_id is None


def test_h_nonexistent_milestone_label_is_unknown(hub):
    r = resolve_version_identifier(hub, "V99")
    assert r.outcome == VersionResolutionOutcome.UNKNOWN


def test_h_pure_decimal_token_is_not_treated_as_a_short_id(hub):
    """A plain number (e.g. a metric threshold like '950000') must never
    be mistaken for a version short ID -- it has no a-f hex letters."""
    r = resolve_version_identifier(hub, "950000")
    assert r.outcome == VersionResolutionOutcome.UNKNOWN


# -- I. empty / non-string input ------------------------------------------------


def test_i_empty_string_is_unknown(hub):
    r = resolve_version_identifier(hub, "   ")
    assert r.outcome == VersionResolutionOutcome.UNKNOWN
    assert r.resolution_status == "empty"


def test_i_non_string_is_unknown(hub):
    r = resolve_version_identifier(hub, None)  # type: ignore[arg-type]
    assert r.outcome == VersionResolutionOutcome.UNKNOWN


# -- J. candidate-token extraction ---------------------------------------------


def test_j_extracts_canonical_keys_bare_hex_and_labels():
    tokens = extract_candidate_version_identifiers("Compare ecdsafail:1196b9f with V5 and also mention a31a47b.")
    assert "ecdsafail:1196b9f" in tokens
    assert "V5" in tokens
    assert "a31a47b" in tokens


def test_j_does_not_extract_pure_decimal_thresholds():
    tokens = extract_candidate_version_identifiers("Which versions have structural Toffoli count below 950000?")
    assert "950000" not in tokens


# -- plan canonicalization ------------------------------------------------------


def test_plan_canonicalization_rewrites_get_metric_version_param(hub):
    plan = QCHQueryPlan.single("get_metric", version="V1", metric="structural.toffoli_count")
    result = canonicalize_plan_versions(hub, plan)
    assert result.outcome == PlanCanonicalizationOutcome.OK
    assert result.plan.steps[0].params["version"] == "ecdsafail:aaa0001"


def test_plan_canonicalization_rewrites_compare_versions_both_sides(hub):
    plan = QCHQueryPlan.single("compare_versions", version_a="V1", version_b="ccc9")
    result = canonicalize_plan_versions(hub, plan)
    assert result.outcome == PlanCanonicalizationOutcome.OK
    assert result.plan.steps[0].params["version_a"] == "ecdsafail:aaa0001"
    assert result.plan.steps[0].params["version_b"] == "ecdsafail:ccc9999"


def test_plan_canonicalization_normalizes_identity_filter_conditions(hub):
    plan = QCHQueryPlan.single("filter_versions", conditions=[{"metric": "version_label", "operator": "==", "value": "V1"}])
    result = canonicalize_plan_versions(hub, plan)
    assert result.outcome == PlanCanonicalizationOutcome.OK
    cond = result.plan.steps[0].params["conditions"][0]
    assert cond["metric"] == "external_version_key"
    assert cond["value"] == "ecdsafail:aaa0001"


def test_plan_canonicalization_leaves_non_identity_conditions_untouched(hub):
    conditions = [{"metric": "structural.toffoli_count", "operator": "<", "value": 950000}]
    plan = QCHQueryPlan.single("filter_versions", conditions=conditions)
    result = canonicalize_plan_versions(hub, plan)
    assert result.outcome == PlanCanonicalizationOutcome.OK
    assert result.plan.steps[0].params["conditions"] == conditions


def test_plan_canonicalization_returns_unknown_entity_for_a_bad_reference(hub):
    plan = QCHQueryPlan.single("get_metric", version="V999", metric="structural.toffoli_count")
    result = canonicalize_plan_versions(hub, plan)
    assert result.outcome == PlanCanonicalizationOutcome.UNKNOWN_ENTITY
    assert result.plan is None
    assert result.offending.raw_identifier == "V999"


def test_plan_canonicalization_returns_ambiguous_for_a_colliding_short_id(hub):
    plan = QCHQueryPlan.single("get_metric", version="bbbb", metric="structural.toffoli_count")
    result = canonicalize_plan_versions(hub, plan)
    assert result.outcome == PlanCanonicalizationOutcome.AMBIGUOUS
    assert result.plan is None
    assert set(result.offending.candidates) == {"ecdsafail:bbbb234", "ecdsafail:bbbb678"}


def test_plan_canonicalization_is_a_no_op_and_preserves_identity_when_no_version_reference_present(hub):
    """A plan with no version-bearing param at all must come back as the
    SAME plan object (not merely an equal one) -- this is what lets
    `qch.nl.answer.build_explanation` correctly omit a redundant
    'original_plan' when nothing was actually repaired/rewritten."""
    plan = QCHQueryPlan.single("list_versions")
    result = canonicalize_plan_versions(hub, plan)
    assert result.outcome == PlanCanonicalizationOutcome.OK
    assert result.plan is plan


def test_plan_canonicalization_multi_step_plan_rewrites_only_the_version_bearing_step(hub):
    plan = QCHQueryPlan(
        steps=(
            QCHQueryStep("traverse", {"version": "V1", "direction": "successors"}),
            QCHQueryStep("limit", {"k": 5}),
        )
    )
    result = canonicalize_plan_versions(hub, plan)
    assert result.outcome == PlanCanonicalizationOutcome.OK
    assert result.plan.steps[0].params["version"] == "ecdsafail:aaa0001"
    assert result.plan.steps[1].params == {"k": 5}
