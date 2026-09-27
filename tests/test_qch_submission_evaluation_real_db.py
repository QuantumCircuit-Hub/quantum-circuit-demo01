"""QCH Phase 2D.5: real-database acceptance for Submission-level official
evaluation. Skipped unless data/qch_ecdsa_p17.sqlite3 exists AND an
official evaluation snapshot has been imported into it.

Every expected value is read INDEPENDENTLY -- from the raw imported
snapshot file (data/external/ecdsafail/evaluation/, verified by SHA-256)
and from read-only SQL -- never from QCH's own query code, and never
hard-coded from the paper.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.nl.backend import DeterministicHeuristicBackend  # noqa: E402
from qch.nl.service import QCHNaturalLanguageService, SystemStatus  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
SNAPSHOT_DIR = PROJECT_ROOT / "data" / "external" / "ecdsafail" / "evaluation"


def _sql(query: str, params: tuple = ()) -> list[tuple]:
    con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        return con.execute(query, params).fetchall()
    finally:
        con.close()


def _has_evaluations() -> bool:
    if not DB_PATH.exists():
        return False
    try:
        return bool(_sql("SELECT count(*) FROM evaluation_snapshot")[0][0])
    except sqlite3.OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _has_evaluations(), reason="real DB with an imported evaluation snapshot not present")


@pytest.fixture(scope="module")
def raw_by_uuid():
    """The imported snapshot's raw records, located by the SHA-256 the DB recorded."""
    sha = _sql("SELECT body_sha256 FROM evaluation_snapshot ORDER BY fetched_at DESC LIMIT 1")[0][0]
    for body in SNAPSHOT_DIR.glob("*.json"):
        if body.name.endswith(".meta.json"):
            continue
        data = body.read_bytes()
        if hashlib.sha256(data).hexdigest() == sha:
            return {r["id"]: r for r in json.loads(data)["submissions"]}
    pytest.skip("raw snapshot file for the imported SHA-256 is not present locally")


@pytest.fixture(scope="module")
def service():
    with QCH.open(str(DB_PATH)) as hub:
        yield QCHNaturalLanguageService(DeterministicHeuristicBackend(), hub)


def _uuid(prefix: str) -> str:
    rows = _sql("SELECT external_submission_key FROM submission WHERE external_submission_key LIKE ?", (f"ecdsafail:{prefix}%",))
    assert len(rows) == 1
    return rows[0][0].split(":", 1)[1]


def test_invariants_unchanged():
    assert _sql("SELECT count(*) FROM circuit_version")[0][0] == 781
    assert _sql("SELECT count(*) FROM submission")[0][0] == 1003
    assert _sql("SELECT count(*) FROM transformation_edge")[0][0] == 784
    assert _sql("SELECT status, count(*) FROM submission GROUP BY 1 ORDER BY 1") == [("PROMOTED", 520), ("SUBMITTED", 222), ("VALIDATED", 261)]


def test_coverage_matches_the_raw_snapshot(raw_by_uuid):
    qch = {k.split(":", 1)[1]: st for k, st in _sql("SELECT external_submission_key, status FROM submission")}
    matched = set(qch) & set(raw_by_uuid)
    snap = _sql("SELECT snapshot_id FROM evaluation_snapshot ORDER BY fetched_at DESC LIMIT 1")[0][0]
    rows = _sql("SELECT s.status, e.official_score IS NOT NULL FROM submission_evaluation e JOIN submission s USING(submission_id) WHERE e.snapshot_id = ?", (snap,))
    assert len(rows) == len(matched)
    for st in ("PROMOTED", "VALIDATED", "SUBMITTED"):
        expected = sum(1 for u in matched if qch[u] == st and raw_by_uuid[u]["officialScore"] is not None)
        assert sum(1 for s, has in rows if s == st and has) == expected


@pytest.mark.parametrize("prefix", ["b6f2b0a", "8e9c9a2", "3616dbf", "b4a51d2"])
def test_four_forensic_cases_equal_the_raw_source(prefix, raw_by_uuid):
    uuid = _uuid(prefix)
    raw = raw_by_uuid[uuid]
    q, t, score, status = _sql(
        "SELECT e.official_peak_qubits, e.official_avg_executed_toffoli, e.official_score, e.platform_status FROM submission_evaluation e"
        " JOIN submission s USING(submission_id) WHERE s.external_submission_key = ?", (f"ecdsafail:{uuid}",))[0]
    assert (q, t, score, status) == (raw["officialMetrics"]["qubits"], raw["officialMetrics"]["toffoli"], raw["officialScore"], raw["status"])


def test_b6f2b0a_submission_only_with_official_evaluation(service, raw_by_uuid):
    uuid = _uuid("b6f2b0a")
    assert _sql("SELECT count(*) FROM circuit_version cv JOIN submission s ON s.submission_id = cv.realized_from_submission_id WHERE s.external_submission_key = ?", (f"ecdsafail:{uuid}",))[0][0] == 0
    raw = raw_by_uuid[uuid]
    r = service.ask("Tell me about b6f2b0a.")
    assert r.status == SystemStatus.ANSWERED
    d = r.raw_result["data"]
    assert d["entity_type"] == "submission" and d["status"]["submission_status"] == "SUBMITTED"
    f = d["evaluation"]["fields"]
    assert (f["evaluation.peak_qubits"], f["evaluation.avg_executed_toffoli"], f["evaluation.score"]) == (raw["officialMetrics"]["qubits"], raw["officialMetrics"]["toffoli"], raw["officialScore"])
    assert f["platform.status"] == raw["status"] and f["platform.rejection_reason"] == raw["rejectionReason"]
    assert d["evaluation"]["provenance"]["source_endpoint"].startswith("https://api.ecdsa.fail/")
    assert "no eligible CircuitVersion" in r.answer or "does not have an eligible CircuitVersion" in r.answer


def test_8e9c9a2_version_and_evaluation(service, raw_by_uuid):
    raw = raw_by_uuid[_uuid("8e9c9a2")]
    d = service.ask("Tell me about 8e9c9a2.").raw_result["data"]
    assert d["identity"]["external_version_key"] == "ecdsafail:60d6185" and d["status"]["submission_status"] == "PROMOTED"
    f = d["evaluation"]["fields"]
    assert (f["evaluation.peak_qubits"], f["evaluation.avg_executed_toffoli"], f["evaluation.score"]) == (raw["officialMetrics"]["qubits"], raw["officialMetrics"]["toffoli"], raw["officialScore"])
    assert f["platform.status"] == raw["status"] and f["platform.promotion_status"] == raw["promotionStatus"]
    assert f["platform.promoted_source_ref"].startswith("60d6185")


def test_3616dbf_structural_and_evaluation_are_both_kept(service, raw_by_uuid):
    raw = raw_by_uuid[_uuid("3616dbf")]
    d = service.ask("Tell me about 3616dbf.").raw_result["data"]
    structural = {name: value for name, value in _sql(
        "SELECT sm.metric_name, sm.metric_value FROM structural_metric sm JOIN circuit_version cv USING(version_id) WHERE cv.external_version_key = 'ecdsafail:897dda2'")}
    shown = {m["name"]: m["value"] for m in d["metrics"]}
    assert shown["structural.toffoli_count"] == structural["structural.toffoli_count"]
    assert d["evaluation"]["fields"]["evaluation.avg_executed_toffoli"] == raw["officialMetrics"]["toffoli"]
    assert shown["structural.toffoli_count"] != d["evaluation"]["fields"]["evaluation.avg_executed_toffoli"]
    assert shown["structural.qubit_count"] == d["evaluation"]["fields"]["evaluation.peak_qubits"] == raw["officialMetrics"]["qubits"]


def test_b4a51d2_lifecycle_and_platform_status_are_independent(service, raw_by_uuid):
    raw = raw_by_uuid[_uuid("b4a51d2")]
    d = service.ask("Tell me about b4a51d2.").raw_result["data"]
    assert d["status"]["submission_status"] == "VALIDATED" and d["identity"]["external_version_key"] == "ecdsafail:364b042"
    assert d["evaluation"]["fields"]["platform.status"] == raw["status"] == "rejected"
    assert d["evaluation"]["fields"]["evaluation.peak_qubits"] == raw["officialMetrics"]["qubits"]


def test_v1_v5_benchmark_rows_equal_the_official_evaluation(service):
    rows = _sql(
        "SELECT cv.version_label, br.toffoli_count, br.peak_qubits, br.score, e.official_avg_executed_toffoli, e.official_peak_qubits, e.official_score"
        " FROM circuit_version cv JOIN benchmark_run br USING(version_id) JOIN submission_evaluation e ON e.submission_id = cv.realized_from_submission_id")
    assert len(rows) == 5
    for label, bt, bq, bs, et, eq, es in rows:
        assert (bt, bq, bs) == (et, eq, es), label
