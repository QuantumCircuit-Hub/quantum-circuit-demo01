"""Tests for the Phase 4A MQT Bench sample ingestion
(scripts/import_mqtbench_sample.py output under data/mqtbench/).
"""

import json
import sys
from pathlib import Path

from qiskit import qasm2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

MQTBENCH_DIR = PROJECT_ROOT / "data" / "mqtbench"
MANIFEST_PATH = MQTBENCH_DIR / "manifest.json"

EXPECTED_COUNT = 10


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _load_all_records() -> list[dict]:
    manifest = _load_manifest()
    records = []
    for entry in manifest["circuits"]:
        path = MQTBENCH_DIR / entry["metadata_file"]
        records.append(json.loads(path.read_text(encoding="utf-8")))
    return records


def test_manifest_contains_exactly_10_entries():
    manifest = _load_manifest()
    assert manifest["total_circuits"] == EXPECTED_COUNT
    assert len(manifest["circuits"]) == EXPECTED_COUNT


def test_exactly_10_mqtbench_circuits_generated():
    records = _load_all_records()
    assert len(records) == EXPECTED_COUNT


def test_every_raw_circuit_file_exists():
    manifest = _load_manifest()
    for entry in manifest["circuits"]:
        raw_path = MQTBENCH_DIR / entry["raw_file"]
        assert raw_path.exists(), f"missing raw file: {raw_path}"


def test_every_metadata_json_exists():
    manifest = _load_manifest()
    for entry in manifest["circuits"]:
        metadata_path = MQTBENCH_DIR / entry["metadata_file"]
        assert metadata_path.exists(), f"missing metadata file: {metadata_path}"


def test_every_circuit_id_is_unique():
    records = _load_all_records()
    circuit_ids = [r["circuit_id"] for r in records]
    assert len(circuit_ids) == len(set(circuit_ids))


def test_every_logical_name_is_unique():
    records = _load_all_records()
    logical_names = [r["logical_name"] for r in records]
    assert len(logical_names) == len(set(logical_names))


def test_every_record_is_version_v1():
    records = _load_all_records()
    for record in records:
        assert record["version"] == "v1"
        assert record["circuit_id"].endswith(":v1")


def test_every_record_has_null_parent_version():
    records = _load_all_records()
    for record in records:
        assert record["provenance"]["parent_version"] is None


def test_every_record_identifies_mqt_bench_source():
    records = _load_all_records()
    for record in records:
        assert record["source"]["dataset"] == "MQT Bench"
        assert record["source"]["generator"] == "mqt-bench"
        assert record["provenance"]["tool"] == "mqt-bench"


def test_every_record_has_non_empty_metrics():
    records = _load_all_records()
    for record in records:
        metrics = record["metrics"]
        assert metrics["num_qubits"] > 0
        assert metrics["gate_count"] > 0
        assert metrics["depth"] > 0
        assert len(metrics["gate_histogram"]) > 0
        assert metrics["gate_count"] == sum(metrics["gate_histogram"].values())


def test_all_raw_circuits_parse_back_successfully():
    manifest = _load_manifest()
    for entry in manifest["circuits"]:
        raw_path = MQTBENCH_DIR / entry["raw_file"]
        circuit = qasm2.load(str(raw_path), custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
        assert circuit.num_qubits > 0
