"""Tests for src/ecdsa_adapter.py (the ECDSA.Fail dataset integration).

These tests read only the small manifest.json (external dataset root, or
the bundled snapshot as a fallback) -- never a .kmx artifact.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import ecdsa_config  # noqa: E402
from ecdsa_adapter import (  # noqa: E402
    ECDSADatasetUnavailable,
    compare_versions,
    load_bundle,
)


def test_bundle_loads_and_exposes_five_versions():
    bundle = load_bundle()
    assert len(bundle["versions"]) == 5
    assert [v["label"] for v in bundle["versions"]] == ["V1", "V2", "V3", "V4", "V5"]


def test_version_ids_are_unique():
    bundle = load_bundle()
    version_ids = [v["version_id"] for v in bundle["versions"]]
    assert len(version_ids) == len(set(version_ids))


def test_historical_edges_resolve_to_existing_versions():
    bundle = load_bundle()
    version_ids = set(bundle["versions_by_id"])
    assert len(bundle["edges"]) == 4
    for edge in bundle["edges"]:
        assert edge["from"] in version_ids
        assert edge["to"] in version_ids
        assert edge["relation"] == "historical_successor"

    graph = bundle["graph"]
    labels = {v["version_id"]: v["label"] for v in bundle["versions"]}
    chain = ["V1", "V2", "V3", "V4", "V5"]
    id_by_label = {label: vid for vid, label in labels.items()}
    for parent_label, child_label in zip(chain, chain[1:]):
        assert graph.has_edge(id_by_label[parent_label], id_by_label[child_label])


def test_benchmark_score_formula_consistency():
    bundle = load_bundle()
    for version in bundle["versions"]:
        assert version["toffoli"] * version["benchmark_qubits"] == version["score"]


def test_versions_have_historical_dates_from_provenance_mapping():
    bundle = load_bundle()
    expected_dates = {
        "V1": "2026-05-30",
        "V2": "2026-05-31",
        "V3": "2026-06-02",
        "V4": "2026-07-07",
        "V5": "2026-09-09",
    }
    for version in bundle["versions"]:
        assert version["date"] == expected_dates[version["label"]]


def test_compare_versions_basic_delta_and_pct_change():
    bundle = load_bundle()
    v1 = bundle["versions_by_id"]["ecdsafail:6f7c159"]
    v2 = bundle["versions_by_id"]["ecdsafail:d19dbb5"]

    comparison = compare_versions(v1, v2)

    assert comparison["operations"]["a"] == v1["operations"]
    assert comparison["operations"]["b"] == v2["operations"]
    assert comparison["operations"]["delta"] == v2["operations"] - v1["operations"]
    expected_pct = (v2["operations"] - v1["operations"]) / v1["operations"] * 100
    assert comparison["operations"]["pct_change"] == pytest.approx(expected_pct)


def test_compare_versions_handles_zero_division_safely():
    fake_a = {"operations": 0, "qubits": 1, "classical_bits": 1, "toffoli": 1, "score": 1}
    fake_b = {"operations": 5, "qubits": 2, "classical_bits": 2, "toffoli": 2, "score": 2}
    comparison = compare_versions(fake_a, fake_b)
    assert comparison["operations"]["pct_change"] is None
    assert comparison["operations"]["delta"] == 5


def test_v4_to_v5_tradeoff_matches_dataset_narrative():
    """The V4 -> V5 transition is the key multi-objective example: more
    operations and qubits, but fewer Toffolis and a lower score."""
    bundle = load_bundle()
    v4 = bundle["versions_by_id"]["ecdsafail:422f21d"]
    v5 = bundle["versions_by_id"]["ecdsafail:a39e07e"]

    comparison = compare_versions(v4, v5)

    assert comparison["operations"]["delta"] > 0
    assert comparison["qubits"]["delta"] > 0
    assert comparison["toffoli"]["delta"] < 0
    assert comparison["score"]["delta"] < 0


def test_missing_external_dataset_falls_back_to_snapshot(monkeypatch):
    """Pointing QCH_ECDSA_DATASET_ROOT at a nonexistent directory must not
    crash -- the adapter should fall back to the bundled snapshot."""
    monkeypatch.setenv(ecdsa_config.ENV_VAR, str(PROJECT_ROOT / "no-such-directory"))
    bundle = load_bundle()
    assert len(bundle["versions"]) == 5
    assert "snapshot" in bundle["source"]


def test_missing_dataset_and_missing_snapshot_raises_clear_error(monkeypatch, tmp_path):
    """When neither the external root nor the bundled snapshot exist, the
    adapter must raise a clear, catchable error instead of crashing the
    whole app -- callers (app.py) use this to fail gracefully for just
    the ECDSA section while GHZ/QFT keep working."""
    monkeypatch.setenv(ecdsa_config.ENV_VAR, str(tmp_path / "no-such-directory"))
    monkeypatch.setattr(ecdsa_config, "SNAPSHOT_PATH", tmp_path / "no-such-snapshot.json")

    import ecdsa_adapter
    monkeypatch.setattr(ecdsa_adapter, "SNAPSHOT_PATH", tmp_path / "no-such-snapshot.json")

    with pytest.raises(ECDSADatasetUnavailable):
        load_bundle()
