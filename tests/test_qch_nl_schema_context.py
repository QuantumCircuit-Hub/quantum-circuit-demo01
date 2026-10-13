"""Tests for qch.nl.schema_context -- the grounding contract loader."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch.nl.schema_context import audit_against_live_registry, load_schema_context  # noqa: E402


@pytest.fixture(scope="module")
def schema():
    return load_schema_context()


def test_schema_loads_all_known_operators(schema):
    for name in ("list_versions", "filter", "sort", "limit", "list_transitions", "compute_delta", "traverse", "group_by", "get_metric", "compare_versions", "filter_versions", "top_k_changes", "rank_transitions"):
        assert schema.is_known_operator(name), f"{name} missing from loaded schema"


def test_schema_matches_live_operator_registry():
    """Catches schema drift: an operator added to qch.query.operators.OPERATORS
    but never documented in qch_query_schema.json, or vice versa."""
    problems = audit_against_live_registry()
    assert problems == [], f"schema/registry drift detected: {problems}"


def test_alias_resolution_is_exact_and_unambiguous(schema):
    assert schema.resolve_exact_metric_name("qubits") == "qubit_count"
    assert schema.resolve_exact_metric_name("peak_qubits") == "qubit_count"
    assert schema.resolve_exact_metric_name("not_a_real_alias") is None


def test_structural_and_stored_metrics_are_distinct_canonical_names(schema):
    assert schema.resolve_exact_metric_name("structural.qubit_count") == "structural.qubit_count"
    assert schema.resolve_exact_metric_name("qubit_count") == "qubit_count"
    # 'qubits' (an alias of the STORED metric) must never resolve to the computed one:
    assert schema.resolve_exact_metric_name("qubits") != "structural.qubit_count"


def test_concept_grouping_disambiguates_via_qualifier(schema):
    resolved, ambiguous = schema.resolve_metric_concepts({"structural", "toffoli", "count"})
    assert resolved == ["structural.toffoli_count"]
    assert ambiguous == []


def test_concept_grouping_flags_bare_concept_as_ambiguous(schema):
    resolved, ambiguous = schema.resolve_metric_concepts({"toffoli", "count"})
    assert resolved == []
    assert ambiguous and set(ambiguous[0]) == {"structural.toffoli_count", "toffoli_count"}


def test_relation_types_loaded(schema):
    for name in ("historical_successor", "next_promoted_commit", "branched_from"):
        assert schema.is_known_relation_type(name)


def test_comparison_operators_loaded(schema):
    for op in ("<", "<=", ">", ">=", "==", "!="):
        assert schema.is_known_comparator(op)
    assert not schema.is_known_comparator("~=")
