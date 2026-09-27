"""READ-ONLY data audit for the QCH-SQL design questions Q1-Q20
(docs/QCH_SQL_Q1_Q20_DATA_AUDIT.md).

Two kinds of evidence, both read-only:
  * `sql`  -- SQLite opened with `mode=ro` (URI), to show what is STORED
              (tables, fields, exact counts), independent of any QCH operator;
  * `qch`  -- the EXISTING QCH query path (qch.query.executor.QCHQueryExecutor
              with the registered operators), to show what QCH can answer today.

It never writes the database (the SHA-256 is printed before and after), never
downloads data, and never modifies application code.

    python audits/qch_sql_q1_q20_audit.py            # human-readable
    python audits/qch_sql_q1_q20_audit.py --json out.json

Paper metric names are kept distinct from QCH names:
  paper Q = peak logical qubits      -> QCH submission_evaluation.official_peak_qubits (evaluation.peak_qubits)
  paper T = average executed Toffoli -> QCH submission_evaluation.official_avg_executed_toffoli (evaluation.avg_executed_toffoli)
  paper S = Q x T                     -> QCH submission_evaluation.official_score (evaluation.score)
Deltas are target - source; percentage change uses the source as denominator.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.query.executor import QCHQueryExecutor  # noqa: E402
from qch.query.models import QCHQueryPlan, QCHQueryStep  # noqa: E402

DB = ROOT / "data" / "qch_ecdsa_p17.sqlite3"
RAW_API_GLOB = "data/external/ecdsafail/evaluation/*.json"  # local raw snapshot (not in QCH tables, not in git)
PAPER_CUTOFF = "2026-07-26T09:21:55Z"  # data cutoff stated in the ECDSA.Fail paper (arXiv 2609.09582v2)
OUT: dict = {}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ro():
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def rows(con, sql, *args):
    return [dict(r) for r in con.execute(sql, args)]


def plan(*steps):
    return QCHQueryPlan(steps=tuple(QCHQueryStep(op, params) for op, params in steps))


def run(hub, *steps):
    return QCHQueryExecutor(hub).execute(plan(*steps))


def show(title, obj, n=5):
    print(f"\n## {title}")
    if isinstance(obj, list):
        print(f"   ({len(obj)} rows)")
        for r in obj[:n]:
            print("   ", r)
    else:
        print("   ", obj)


# Version-level view of the official metrics: each CircuitVersion is realized
# from at most one Submission; its official Q/T/S are that submission's latest
# platform evaluation.
VERSION_METRICS_SQL = """
SELECT v.version_id, v.external_version_key AS vkey, v.version_label, v.historical_time,
       s.status AS lifecycle, e.platform_status, e.official_peak_qubits AS Q,
       e.official_avg_executed_toffoli AS T, e.official_score AS S,
       e.platform_created_at, e.promotion_finished_at, e.rejection_reason,
       substr(s.external_submission_key, 11) AS submission_uuid
FROM circuit_version v
LEFT JOIN submission s ON s.submission_id = v.realized_from_submission_id
LEFT JOIN submission_evaluation e ON e.submission_id = s.submission_id
"""


def version_metrics(con):
    return {r["version_id"]: r for r in rows(con, VERSION_METRICS_SQL)}


def edges(con, relation):
    return rows(con, "SELECT source_version_id AS src, target_version_id AS dst FROM transformation_edge WHERE relation_type = ?", relation)


def pareto(points):
    """points: [(Q, T, key)] -> nondominated (minimise Q and T; dominance needs
    <= in both and < in at least one). Exact ties are all retained."""
    front = []
    for q, t, k in points:
        if not any((q2 <= q and t2 <= t) and (q2 < q or t2 < t) for q2, t2, _ in points):
            front.append((q, t, k))
    return sorted(front)


def main() -> None:
    before = sha(DB)
    print(f"DB {DB.relative_to(ROOT).as_posix()} sha256 before: {before}")
    con = ro()
    vm = version_metrics(con)

    # ---------------------------------------------------------------- inventory
    counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}
    OUT["table_counts"] = counts
    show("table row counts", counts)
    cov = {
        "logical_circuits": rows(con, "SELECT logical_circuit_id, name FROM logical_circuit"),
        "versions_total": counts["circuit_version"],
        "versions_by_lifecycle": rows(con, "SELECT s.status AS lifecycle, count(*) AS n FROM circuit_version v JOIN submission s ON s.submission_id = v.realized_from_submission_id GROUP BY 1"),
        "milestone_versions": rows(con, "SELECT v.version_label, v.external_version_key FROM version_tag t JOIN circuit_version v USING (version_id) ORDER BY v.sequence_no"),
        "submissions_by_lifecycle": rows(con, "SELECT status, count(*) AS n FROM submission GROUP BY 1"),
        "evaluations_by_platform_status": rows(con, "SELECT platform_status, count(*) AS n, sum(official_score IS NOT NULL) AS scored FROM submission_evaluation GROUP BY 1"),
        "scored_submissions": con.execute("SELECT count(*) FROM submission_evaluation WHERE official_score IS NOT NULL").fetchone()[0],
        "edges_by_relation": rows(con, "SELECT relation_type, count(*) AS n, count(DISTINCT source_version_id) AS distinct_sources FROM transformation_edge GROUP BY 1"),
        "edge_annotations": rows(con, "SELECT count(*) AS n, sum(transformation_name IS NOT NULL) AS named, sum(description IS NOT NULL) AS described, sum(evidence_json NOT IN ('{}', '') AND evidence_json IS NOT NULL) AS with_evidence FROM transformation_edge"),
        "source_commits": rows(con, "SELECT count(*) AS n, sum(commit_time IS NULL) AS no_time, sum(parent_commit_sha IS NULL) AS no_parent FROM source_commit"),
        "submission_source_commit_roles": rows(con, "SELECT role, count(*) AS n FROM submission_source_commit GROUP BY 1"),
        "verification_results": rows(con, "SELECT verification_type, status, count(*) AS n, count(DISTINCT version_id) AS versions FROM verification_result GROUP BY 1, 2"),
        "structural_metrics": rows(con, "SELECT metric_name, count(DISTINCT version_id) AS versions FROM structural_metric GROUP BY 1"),
        "benchmark_runs": rows(con, "SELECT v.version_label, b.peak_qubits, b.toffoli_count, b.score, b.result_json FROM benchmark_run b JOIN circuit_version v USING (version_id) ORDER BY v.sequence_no"),
        "artifacts": rows(con, "SELECT artifact_type, format, count(DISTINCT version_id) AS versions FROM artifact GROUP BY 1, 2"),
        "evaluation_snapshot": rows(con, "SELECT source_endpoint, benchmark_source_ref, fetched_at, record_count, importer_name, importer_version FROM evaluation_snapshot"),
        "versions_with_official_score": sum(1 for r in vm.values() if r["S"] is not None),
        "versions_without_historical_time": [r["version_label"] or r["vkey"] for r in vm.values() if r["historical_time"] is None],
    }
    raw = sorted(ROOT.glob(RAW_API_GLOB))
    if raw:
        subs = json.loads(raw[0].read_text(encoding="utf-8"))["submissions"]
        in_qch = {r["source_submission_uuid"] for r in rows(con, "SELECT source_submission_uuid FROM submission_evaluation")}
        cov["raw_api_snapshot"] = {"file": raw[0].relative_to(ROOT).as_posix(), "records": len(subs),
                                   "fields": sorted(subs[0].keys()), "official_metric_fields": sorted({k for s in subs for k in (s.get("officialMetrics") or {})}),
                                   "records_not_in_qch": sum(1 for s in subs if s["id"] not in in_qch)}
    OUT["coverage"] = cov
    for k, v in cov.items():
        show(f"coverage: {k}", v, n=8)

    # ------------------------------------------------------------ metric semantics
    sem = {
        "S_equals_QxT_on_every_scored_record": con.execute(
            "SELECT count(*) FROM submission_evaluation WHERE official_score IS NOT NULL AND official_peak_qubits * official_avg_executed_toffoli != official_score").fetchone()[0] == 0,
        "zero_or_null_official_values": rows(con, "SELECT sum(official_peak_qubits = 0) AS q_zero, sum(official_avg_executed_toffoli = 0) AS t_zero, sum(official_score IS NULL) AS s_null FROM submission_evaluation")[0],
        "milestones_benchmark_vs_official": rows(con, """
            SELECT v.version_label, b.peak_qubits AS bench_Q, b.toffoli_count AS bench_T, b.score AS bench_S,
                   e.official_peak_qubits AS official_Q, e.official_avg_executed_toffoli AS official_T, e.official_score AS official_S,
                   (SELECT metric_value FROM structural_metric m WHERE m.version_id = v.version_id AND m.metric_name = 'qubits') AS manifest_qubits
            FROM circuit_version v JOIN benchmark_run b USING (version_id)
            LEFT JOIN submission_evaluation e ON e.submission_id = v.realized_from_submission_id ORDER BY v.sequence_no"""),
        "structural_vs_official": rows(con, """
            SELECT v.external_version_key AS vkey,
                   max(CASE WHEN m.metric_name = 'structural.qubit_count' THEN m.metric_value END) AS structural_qubits,
                   e.official_peak_qubits AS official_Q,
                   max(CASE WHEN m.metric_name = 'structural.toffoli_count' THEN m.metric_value END) AS structural_toffoli,
                   e.official_avg_executed_toffoli AS official_T
            FROM circuit_version v JOIN structural_metric m USING (version_id)
            LEFT JOIN submission_evaluation e ON e.submission_id = v.realized_from_submission_id
            WHERE m.metric_name LIKE 'structural.%' GROUP BY v.version_id HAVING official_Q IS NOT NULL"""),
    }
    sv = sem["structural_vs_official"]
    sem["structural_vs_official_summary"] = {
        "versions_with_both": len(sv),
        "structural_qubits_equal_official_Q": sum(r["structural_qubits"] == r["official_Q"] for r in sv),
        "structural_toffoli_ge_official_T": sum(r["structural_toffoli"] >= r["official_T"] for r in sv),
        "structural_toffoli_equal_official_T": sum(r["structural_toffoli"] == r["official_T"] for r in sv),
    }
    OUT["semantics"] = sem
    for k, v in sem.items():
        show(f"semantics: {k}", v, n=6)

    hub = QCH.open(str(DB))
    Q = {}
    try:
        # Q1 -- version inventory (existing path: list_versions -> sort by historical_time)
        r = run(hub, ("list_versions", {}), ("sort", {"by": "historical_time", "order": "asc"}))
        src = {x["version_id"]: x for x in rows(con, """
            SELECT v.version_id, sc.commit_sha, sc.commit_time FROM circuit_version v
            JOIN submission_source_commit ssc ON ssc.submission_id = v.realized_from_submission_id
            JOIN source_commit sc ON sc.source_commit_id = ssc.source_commit_id
            WHERE ssc.role = (CASE WHEN (SELECT status FROM submission WHERE submission_id = v.realized_from_submission_id) = 'PROMOTED' THEN 'promoted' ELSE 'validated' END)""")}
        match = sum(1 for v in r.data if v["version_id"] in src and src[v["version_id"]]["commit_time"] == v["historical_time"])
        Q["Q1"] = {"qch_status": r.status.value, "rows": len(r.data), "first": [(v["external_version_key"], v["historical_time"]) for v in r.data[:3]],
                   "null_time_sorted_last": [v["version_label"] or v["external_version_key"] for v in r.data if v["historical_time"] is None],
                   "versions_with_full_source_commit": len(src), "historical_time_equals_commit_time": match}

        # Q2 -- score trajectory (existing path: list_submissions carries official Q/T/S; version key per record)
        r = run(hub, ("list_submissions", {}), ("filter", {"conditions": [{"metric": "evaluation.score", "operator": ">", "value": 0}]}),
                ("sort", {"by": "platform.created_at", "order": "asc"}))
        Q["Q2"] = {"qch_status": r.status.value, "scored_submissions": len(r.data),
                   "scored_with_circuit_version": sum(1 for x in r.data if x.get("external_version_key")),
                   "sample": [(x["submission_uuid"][:8], x.get("external_version_key"), x["evaluation.peak_qubits"], x["evaluation.avg_executed_toffoli"], x["evaluation.score"]) for x in r.data[:3]],
                   "S_equals_QxT": all(x["evaluation.peak_qubits"] * x["evaluation.avg_executed_toffoli"] == x["evaluation.score"] for x in r.data)}

        # Q3 -- best verified at a cutoff ("verified" := platform accepted; time := platform.created_at)
        def best_at(cutoff):
            base = [("list_submissions", {"platform_status": "accepted"}),
                    ("filter", {"conditions": [{"metric": "platform.created_at", "operator": "<=", "value": cutoff}, {"metric": "evaluation.score", "operator": ">", "value": 0}]}),
                    ("sort", {"by": "evaluation.score", "order": "asc"})]
            full = run(hub, *base).data
            best = full[0]["evaluation.score"] if full else None
            return [(x["submission_uuid"][:8], x.get("external_version_key"), x["evaluation.peak_qubits"], x["evaluation.avg_executed_toffoli"], x["evaluation.score"], x["platform.created_at"]) for x in full if x["evaluation.score"] == best]
        Q["Q3"] = {"cutoff": PAPER_CUTOFF, "best_ties_retained": best_at(PAPER_CUTOFF), "best_at_latest": best_at("9999")}

        # Q4 -- first T < 1,000,000
        r = run(hub, ("list_submissions", {}), ("filter", {"conditions": [{"metric": "evaluation.avg_executed_toffoli", "operator": "<", "value": 1000000}]}),
                ("sort", {"by": "platform.created_at", "order": "asc"}), ("limit", {"k": 3}))
        Q["Q4"] = {"qch_status": r.status.value, "earliest": [(x["submission_uuid"][:8], x.get("external_version_key"), x["platform.status"], x["evaluation.avg_executed_toffoli"], x["platform.created_at"]) for x in r.data]}
        if raw:
            api_first = sorted((s["createdAt"], s["id"][:8], s["status"], (s.get("officialMetrics") or {}).get("toffoli")) for s in subs
                               if (s.get("officialMetrics") or {}).get("toffoli") is not None and s["officialMetrics"]["toffoli"] < 1000000)[:3]
            Q["Q4"]["raw_api_earliest_all_1248"] = api_first
        accepted_first = run(hub, ("list_submissions", {"platform_status": "accepted"}),
                             ("filter", {"conditions": [{"metric": "evaluation.avg_executed_toffoli", "operator": "<", "value": 1000000}]}),
                             ("sort", {"by": "platform.created_at", "order": "asc"}), ("limit", {"k": 1})).data
        Q["Q4"]["earliest_accepted"] = [(x["submission_uuid"][:8], x.get("external_version_key"), x["evaluation.avg_executed_toffoli"], x["platform.created_at"]) for x in accepted_first]

        # Q5-Q8 -- next_promoted_commit transitions over the OFFICIAL metrics.
        # Existing path check: compute_delta over evaluation.score yields no values (version-scoped metric reader).
        r = run(hub, ("list_transitions", {"relation_type": "next_promoted_commit"}), ("compute_delta", {"metric": "evaluation.score"}))
        Q["Q5_existing_path"] = {"qch_status": r.status.value, "transitions": len(r.data), "with_evaluation_score_delta": sum(1 for x in r.data if x.get("evaluation.score_delta") is not None)}
        npc = edges(con, "next_promoted_commit")
        trans = []
        for e in npc:
            a, b = vm[e["src"]], vm[e["dst"]]
            trans.append({"src": a["vkey"], "dst": b["vkey"], "Qa": a["Q"], "Qb": b["Q"], "Ta": a["T"], "Tb": b["T"], "Sa": a["S"], "Sb": b["S"]})
        both = [t for t in trans if t["Sa"] is not None and t["Sb"] is not None]
        neg = [t for t in both if t["Sb"] - t["Sa"] < 0]
        Q["Q5"] = {"npc_edges": len(npc), "both_endpoints_scored": len(both), "negative_score_delta": len(neg),
                   "sample": [(t["src"], t["dst"], t["Sb"] - t["Sa"]) for t in neg[:3]]}
        q6 = [t for t in both if t["Qb"] > t["Qa"] and t["Tb"] < t["Ta"]]
        Q["Q6"] = {"both_endpoints_scored": len(both), "Q_up_T_down": len(q6), "sample": [(t["src"], t["dst"], t["Qb"] - t["Qa"], t["Tb"] - t["Ta"]) for t in q6[:3]]}
        q7 = [t for t in q6 if t["Ta"] and (t["Tb"] - t["Ta"]) / t["Ta"] * 100 <= -10]
        Q["Q7"] = {"source_T_zero": sum(1 for t in trans if t["Ta"] == 0), "source_or_target_missing": len(trans) - len(both), "matches": len(q7),
                   "sample": [(t["src"], t["dst"], round((t["Tb"] - t["Ta"]) / t["Ta"] * 100, 2), t["Qb"] - t["Qa"]) for t in q7[:3]]}
        q8 = [t for t in both if (t["Sb"] - t["Sa"]) / t["Sa"] * 100 <= -10]
        Q["Q8"] = {"matches": len(q8), "sample": [(t["src"], t["dst"], round((t["Sb"] - t["Sa"]) / t["Sa"] * 100, 2)) for t in q8[:5]]}

        # Q9/Q10 -- Pareto (minimise Q, T) over accepted versions/submissions
        acc = rows(con, "SELECT substr(s.external_submission_key, 11) AS uuid, v.external_version_key AS vkey, e.official_peak_qubits AS Q, e.official_avg_executed_toffoli AS T, e.official_score AS S, e.platform_created_at AS created_at, e.promotion_finished_at AS promoted_at "
                        "FROM submission_evaluation e JOIN submission s USING (submission_id) LEFT JOIN circuit_version v ON v.realized_from_submission_id = s.submission_id "
                        "WHERE e.platform_status = 'accepted' AND e.official_score IS NOT NULL")
        f9 = pareto([(x["Q"], x["T"], x["vkey"] or x["uuid"][:8]) for x in acc])
        Q["Q9"] = {"accepted_scored": len(acc), "frontier_size": len(f9), "frontier": f9}
        f10 = pareto([(x["Q"], x["T"], x["vkey"] or x["uuid"][:8]) for x in acc if x["created_at"] <= PAPER_CUTOFF])
        f10p = pareto([(x["Q"], x["T"], x["vkey"] or x["uuid"][:8]) for x in acc if x["promoted_at"] and x["promoted_at"] <= PAPER_CUTOFF])
        Q["Q10"] = {"cutoff": PAPER_CUTOFF, "by_submission_time": {"eligible": sum(1 for x in acc if x["created_at"] <= PAPER_CUTOFF), "frontier": f10},
                    "by_promotion_time": {"eligible": sum(1 for x in acc if x["promoted_at"] and x["promoted_at"] <= PAPER_CUTOFF), "frontier": f10p}}

        # Q11 -- Toffoli depth anywhere?
        depth_cols = [(t, c) for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'") for c in [r[1] for r in con.execute(f"PRAGMA table_info({t})")] if "depth" in c.lower()]
        depth_metrics = rows(con, "SELECT metric_name, count(*) AS n FROM structural_metric WHERE metric_name LIKE '%depth%' GROUP BY 1")
        Q["Q11"] = {"depth_columns": depth_cols, "depth_metrics": depth_metrics}

        # Q12 -- best score vs lowest width (ties retained), accepted only
        best_s = min(x["S"] for x in acc); min_q = min(x["Q"] for x in acc)
        Q["Q12"] = {"best_score": [(x["vkey"], x["uuid"][:8], x["Q"], x["T"], x["S"]) for x in acc if x["S"] == best_s],
                    "lowest_width": [(x["vkey"], x["uuid"][:8], x["Q"], x["T"], x["S"]) for x in acc if x["Q"] == min_q]}
        r = run(hub, ("list_submissions", {"platform_status": "accepted"}), ("filter", {"conditions": [{"metric": "evaluation.peak_qubits", "operator": "==", "value": min_q}]}))
        Q["Q12"]["qch_lowest_width_rows"] = len(r.data)

        # Q13 -- explicit branch labels?
        Q["Q13"] = {"version_tags": rows(con, "SELECT tag, count(*) AS n FROM version_tag GROUP BY 1"),
                    "branched_from_edges": len(edges(con, "branched_from")),
                    "named_branches_in_promoted_source_ref": rows(con, "SELECT count(*) AS n, sum(promoted_source_ref LIKE 'refs/%') AS ref_names FROM submission_evaluation WHERE promoted_source_ref IS NOT NULL")[0]}

        # Q14 -- nearest common ancestors (curated relations and Git parent SHAs)
        parents = defaultdict(set)
        for rel in ("next_promoted_commit", "branched_from"):
            for e in edges(con, rel):
                parents[e["dst"]].add(e["src"])
        key_to_id = {r["vkey"]: vid for vid, r in vm.items()}

        def ancestors(vid):
            dist, dq = {vid: 0}, deque([vid])
            while dq:
                cur = dq.popleft()
                for p in parents[cur]:
                    if p not in dist:
                        dist[p] = dist[cur] + 1
                        dq.append(p)
            return dist
        best_v = [x["vkey"] for x in acc if x["S"] == best_s and x["vkey"]]
        low_v = [x["vkey"] for x in acc if x["Q"] == min_q and x["vkey"]]
        nca = []
        if best_v and low_v:
            da, db = ancestors(key_to_id[best_v[0]]), ancestors(key_to_id[low_v[0]])
            common = {k: da[k] + db[k] for k in da.keys() & db.keys()}
            m = min(common.values()) if common else None
            nca = [(vm[k]["vkey"], da[k], db[k]) for k, d in common.items() if d == m]
        commits = {r["commit_sha"]: r["parent_commit_sha"] for r in rows(con, "SELECT commit_sha, parent_commit_sha FROM source_commit")}
        unresolvable_parents = sum(1 for p in commits.values() if p and p not in commits)
        Q["Q14"] = {"best_score_version": best_v, "lowest_width_version": low_v, "nearest_common_ancestors_curated": nca,
                    "git_commits_in_store": len(commits), "git_parent_sha_not_in_store": unresolvable_parents}

        # Q15/Q16 -- technique annotations
        Q["Q15"] = {"edge_annotations": cov["edge_annotations"], "commit_messages_present": con.execute("SELECT count(*) FROM source_commit WHERE message IS NOT NULL AND message != ''").fetchone()[0]}

        # Q17 -- per-version verification checks
        Q["Q17"] = {"verification_results": cov["verification_results"], "per_check_fields_in_evaluation_table": [c[1] for c in con.execute("PRAGMA table_info(submission_evaluation)") if any(k in c[1] for k in ("ancilla", "phase", "value", "correct", "clean"))],
                    "raw_api_official_metric_fields": cov.get("raw_api_snapshot", {}).get("official_metric_fields")}

        # Q18 -- failed/rejected candidates cheaper than their parent (branched_from parent)
        q18 = []
        for e in edges(con, "branched_from"):
            child, parent = vm[e["dst"]], vm[e["src"]]
            if child["platform_status"] in ("rejected", "failed") and child["S"] is not None and parent["S"] is not None and child["S"] < parent["S"]:
                q18.append((child["vkey"], child["platform_status"], child["S"], parent["vkey"], parent["S"], child["rejection_reason"]))
        unversioned = rows(con, "SELECT e.platform_status, count(*) AS n, sum(e.official_score IS NOT NULL) AS scored FROM submission s JOIN submission_evaluation e USING (submission_id) WHERE s.status = 'SUBMITTED' GROUP BY 1")
        Q["Q18"] = {"matches_with_parent_edge": len(q18), "sample": q18[:4], "failed_or_rejected_without_circuit_version": unversioned,
                    "branched_children_by_platform_status": rows(con, "SELECT e.platform_status, count(*) AS n, sum(e.official_score IS NOT NULL) AS scored FROM transformation_edge t JOIN circuit_version v ON v.version_id = t.target_version_id JOIN submission_evaluation e ON e.submission_id = v.realized_from_submission_id WHERE t.relation_type = 'branched_from' GROUP BY 1")}

        # Q19 -- reproduction materials per version
        with_commit = {x["version_id"] for x in rows(con, "SELECT DISTINCT v.version_id FROM circuit_version v JOIN submission_source_commit ssc ON ssc.submission_id = v.realized_from_submission_id") } | \
                      {x["version_id"] for x in rows(con, "SELECT version_id FROM version_source")}
        with_artifact = {x["version_id"] for x in rows(con, "SELECT DISTINCT version_id FROM artifact WHERE status = 'READY'")}
        with_eval_version = {x["version_id"] for x in rows(con, "SELECT DISTINCT version_id FROM benchmark_run WHERE benchmark_version IS NOT NULL")}
        Q["Q19"] = {"versions_with_source_commit": len(with_commit), "versions_with_ready_artifact": len(with_artifact),
                    "versions_with_per_version_evaluator_version": len(with_eval_version),
                    "snapshot_level_evaluator_ref": cov["evaluation_snapshot"][0]["benchmark_source_ref"],
                    "commit_and_artifact": len(with_commit & with_artifact),
                    "materialization_jobs": rows(con, "SELECT status, materializer_name, materializer_version, count(*) AS n FROM materialization_job GROUP BY 1, 2, 3")}

        # Q20 -- later versions better on both objectives than a real reference (8e9c9a2, the paper's cutoff best)
        ref = run(hub, ("list_submissions", {}), ("filter", {"conditions": [{"metric": "external_submission_key", "operator": "==", "value": "ecdsafail:8e9c9a22-a6ec-4950-8c5b-c12aade0bc8e"}]})).data[0]
        r = run(hub, ("list_submissions", {"platform_status": "accepted"}),
                ("filter", {"conditions": [{"metric": "platform.created_at", "operator": ">", "value": ref["platform.created_at"]},
                                           {"metric": "evaluation.score", "operator": "<", "value": ref["evaluation.score"]},
                                           {"metric": "evaluation.peak_qubits", "operator": "<", "value": ref["evaluation.peak_qubits"]}]}),
                ("sort", {"by": "platform.created_at", "order": "asc", "tie_breaker": "submission_uuid"}))
        Q["Q20"] = {"reference": (ref["external_version_key"], ref["evaluation.peak_qubits"], ref["evaluation.score"], ref["platform.created_at"]),
                    "qch_status": r.status.value, "matches": len(r.data),
                    "sample": [(x.get("external_version_key"), x["evaluation.peak_qubits"], x["evaluation.score"], x["platform.created_at"]) for x in r.data[:3]]}
    finally:
        hub.close()
        con.close()

    OUT["questions"] = Q
    for k, v in Q.items():
        show(k, v, n=6)

    # ---------------------------------------------------------------- revision pass
    rev, checks = revision_pass(raw)
    OUT["revision"] = rev
    OUT["checks"] = checks
    for k, v in rev.items():
        show(f"revision: {k}", v, n=12)
    print("\n## targeted checks")
    for name, ok in checks.items():
        print(f"   [{'PASS' if ok else 'FAIL'}] {name}")

    after = sha(DB)
    OUT["db_sha256"] = {"before": before, "after": after, "unchanged": before == after}
    print(f"\nDB sha256 after: {after}  unchanged={before == after}")
    if "--json" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--json") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(OUT, indent=2, default=str), encoding="utf-8")
        print(f"wrote {out}")
    if not all(checks.values()) or before != after:
        sys.exit(1)


# ====================================================================== revision pass
# Reference values TRANSCRIBED from the local paper (../2609.09582v2.pdf, Table 2:
# "Archive-conditioned Pareto set at the data cutoff"). They are the comparison
# target, never inserted into QCH.
PAPER_TABLE2 = [
    ("b6f2b0a", 825, 489161900, "Admitted"), ("f1d8707", 826, 479337428, "Admitted"),
    ("39a9b5f", 828, 448102916, "Admitted"), ("a3b0148", 948, 54781961, "Admitted"),
    ("4352cfb", 980, 28847321, "Admitted"), ("1c0e0e9", 1133, 1460511, "Admitted"),
    ("8e9c9a2", 1151, 1299453, "Promoted"), ("a536a48", 1158, 1292651, "Promoted"),
]
PAPER_LATER_LOW_Q = ("b4a51d2", 813)  # paper: "A later submission, b4a51d2, reported 813 qubits"
# Optional EXTERNAL local archive (a Git clone of the challenge repository, outside
# this repo). Read-only git commands only; everything degrades to "unavailable".
GIT_REPO = Path(__import__("os").environ.get("QCH_ECDSA_GIT_REPO", str(ROOT.parent.parent / "Data" / "ECDSA" / "ecdsafail-challenge")))


def git(*args):
    import subprocess

    if not (GIT_REPO / ".git").exists():
        return None
    return subprocess.run(["git", "-C", str(GIT_REPO), *args], capture_output=True, text=True).stdout.strip()


def git_ok(*args):
    import subprocess

    return (GIT_REPO / ".git").exists() and subprocess.run(["git", "-C", str(GIT_REPO), *args], capture_output=True).returncode == 0


SUBMISSIONS_SQL = """
SELECT substr(s.external_submission_key, 11) AS uuid, s.submission_id, s.status AS lifecycle,
       e.platform_status, e.rejection_reason, e.official_peak_qubits AS Q, e.official_avg_executed_toffoli AS T,
       e.official_score AS S, e.submission_commit_sha, e.platform_created_at AS created_at,
       e.promotion_finished_at AS promoted_at, v.version_id, v.external_version_key AS vkey
FROM submission s
LEFT JOIN submission_evaluation e ON e.submission_id = s.submission_id
LEFT JOIN circuit_version v ON v.realized_from_submission_id = s.submission_id
"""


def revision_pass(raw_files):
    con = ro()
    subs = rows(con, SUBMISSIONS_SQL)
    by_prefix = lambda p: [s for s in subs if s["uuid"].startswith(p)]  # noqa: E731
    raw = json.loads(raw_files[0].read_text(encoding="utf-8"))["submissions"] if raw_files else []
    raw_by_prefix = lambda p: [r for r in raw if r["id"].startswith(p)]  # noqa: E731
    roles = defaultdict(dict)
    for r in rows(con, "SELECT ssc.submission_id, ssc.role, sc.commit_sha FROM submission_source_commit ssc JOIN source_commit sc USING (source_commit_id)"):
        roles[r["submission_id"]][r["role"]] = r["commit_sha"]
    rev, checks = {"git_archive": str(GIT_REPO.name) if (GIT_REPO / ".git").exists() else "unavailable"}, {}

    # 1. identifier mapping: paper ID = 7-char prefix of the platform submission UUID
    mapping = []
    for p in ("3616dbf", "8e9c9a2", "a536a48", "3ccbe24", "b6f2b0a", "b4a51d2"):
        (s,) = by_prefix(p)
        mapping.append({"paper_id": p, "platform_submission_uuid": s["uuid"], "lifecycle": s["lifecycle"], "platform_status": s["platform_status"],
                        "submission_commit_sha": (s["submission_commit_sha"] or "")[:10], "commit_roles": {k: v[:10] for k, v in roles[s["submission_id"]].items()},
                        "qch_version_key": s["vkey"], "qch_version_id": s["version_id"]})
    rev["identifier_mapping"] = mapping
    m = {x["paper_id"]: x for x in mapping}
    checks["id: 3616dbf -> uuid 3616dbf2..., commit 897dda2 (submitted=validated=promoted), version ecdsafail:897dda2"] = (
        m["3616dbf"]["platform_submission_uuid"].startswith("3616dbf2") and m["3616dbf"]["submission_commit_sha"].startswith("897dda2")
        and m["3616dbf"]["qch_version_key"] == "ecdsafail:897dda2" and set(m["3616dbf"]["commit_roles"].values()) == {"897dda2"})
    checks["id: 8e9c9a2 -> uuid 8e9c9a22..., submitted ae7676d, promoted 60d6185 = version ecdsafail:60d6185"] = (
        m["8e9c9a2"]["commit_roles"].get("submitted", "").startswith("ae7676d") and m["8e9c9a2"]["commit_roles"].get("promoted", "").startswith("60d6185")
        and m["8e9c9a2"]["qch_version_key"] == "ecdsafail:60d6185")

    # 2. the paper's eight Table 2 points: availability by source
    eight = []
    for pid, q, t, pop in PAPER_TABLE2:
        qs, rs = by_prefix(pid), raw_by_prefix(pid)
        s = qs[0] if qs else None
        sha_full = s["submission_commit_sha"] if s else None
        eight.append({
            "paper_id": pid, "paper_Q": q, "paper_T": t, "paper_population": pop,
            "qch_submission": s["uuid"] if s else None, "qch_Q_T_match": bool(s and s["Q"] == q and s["T"] == t),
            "qch_platform_status": s["platform_status"] if s else None, "qch_lifecycle": s["lifecycle"] if s else None,
            "qch_rejection_reason": s["rejection_reason"] if s else None, "qch_version_key": s["vkey"] if s else None,
            "qch_commit_in_source_commit": bool(sha_full and con.execute("SELECT 1 FROM source_commit WHERE commit_sha IN (?, substr(?, 1, 7))", (sha_full, sha_full)).fetchone()),
            "raw_api": bool(rs), "raw_api_Q_T_match": bool(rs and (rs[0].get("officialMetrics") or {}) == {"qubits": q, "toffoli": t}),
            "git_commit_present": git_ok("cat-file", "-e", f"{sha_full}^{{commit}}") if sha_full else False,
            "git_submission_branch": bool(s and git("branch", "-a", "--list", f"origin/submissions/{s['uuid']}")),
            "created_at": s["created_at"] if s else None})
    rev["paper_table2_eight_points"] = eight
    checks["all 8 paper Table 2 points exist in QCH submissions with identical Q and T"] = all(x["qch_Q_T_match"] for x in eight)
    checks["all 8 paper Table 2 points exist in the raw API snapshot with identical Q and T"] = all(x["raw_api_Q_T_match"] for x in eight)
    checks["paper 'Admitted' points are platform-rejected ('score did not improve current best'), 'Promoted' are accepted"] = all(
        (x["qch_platform_status"] == "rejected" and x["qch_rejection_reason"] == "score did not improve current best") if x["paper_population"] == "Admitted" else x["qch_platform_status"] == "accepted"
        for x in eight)
    checks["6 'Admitted' points have NO QCH CircuitVersion; 2 'Promoted' do"] = all((x["qch_version_key"] is None) == (x["paper_population"] == "Admitted") for x in eight)

    # 2b. frontiers by explicitly named population and date axis
    def front(pop):
        return sorted((p["Q"], p["T"], p["uuid"][:7], p["platform_status"]) for p in pop
                      if not any(o["Q"] <= p["Q"] and o["T"] <= p["T"] and (o["Q"] < p["Q"] or o["T"] < p["T"]) for o in pop))
    scored = [s for s in subs if s["S"] is not None]
    accepted = [s for s in scored if s["platform_status"] == "accepted"]
    pops = {
        "P1 accepted, all time": accepted,
        "P2 accepted, created_at <= cutoff": [s for s in accepted if s["created_at"] <= PAPER_CUTOFF],
        "P3 accepted, promotion_finished_at <= cutoff": [s for s in accepted if s["promoted_at"] and s["promoted_at"] <= PAPER_CUTOFF],
        "P4 evaluated (accepted + rejected-with-metrics), created_at <= cutoff": [s for s in scored if s["created_at"] <= PAPER_CUTOFF],
        "P5 evaluated (accepted + rejected-with-metrics), all time": scored,
    }
    paper_ids = {pid for pid, *_ in PAPER_TABLE2}
    frontiers = {}
    for name, pop in pops.items():
        f = front(pop)
        ids = {x[2] for x in f}
        frontiers[name] = {"population_size": len(pop), "frontier_size": len(f), "frontier": f,
                           "equals_paper_table2": ids == paper_ids, "paper_points_missing": sorted(paper_ids - ids), "extra_points": len(ids - paper_ids)}
    rev["pareto_frontiers_by_population"] = frontiers
    p4 = pops["P4 evaluated (accepted + rejected-with-metrics), created_at <= cutoff"]
    dominators = {}
    for pid, q, t, _ in PAPER_TABLE2:
        dom = sorted((o["Q"], o["T"], o["uuid"][:7], o["created_at"][:10]) for o in p4 if o["Q"] <= q and o["T"] <= t and (o["Q"] < q or o["T"] < t))
        if dom:
            dominators[pid] = dom[:3]
    rev["paper_points_dominated_within_P4"] = dominators
    checks["P2 (accepted, created<=cutoff) frontier = the paper's 2 promoted points"] = {x[2] for x in frontiers["P2 accepted, created_at <= cutoff"]["frontier"]} == {"8e9c9a2", "a536a48"}
    checks["no stored population reproduces the paper's 8-point set (curation not recorded)"] = not any(v["equals_paper_table2"] for v in frontiers.values())
    checks["ddb641a (Q=824, created before cutoff) is stored and dominates b6f2b0a"] = any(d[2] == "ddb641a" for d in dominators.get("b6f2b0a", []))

    # 3. lowest width by population
    def lowest(pop):
        mq = min(p["Q"] for p in pop)
        return {"Q": mq, "rows": [(p["uuid"][:7], p["vkey"], p["platform_status"], p["T"], p["created_at"][:10]) for p in pop if p["Q"] == mq]}
    rev["lowest_width_by_population"] = {name: lowest(pop) for name, pop in pops.items()}
    later = by_prefix(PAPER_LATER_LOW_Q[0])
    rev["paper_low_width_points"] = {
        "b6f2b0a (paper: Q=825 at cutoff)": next(x for x in eight if x["paper_id"] == "b6f2b0a"),
        "b4a51d2 (paper: later Q=813)": {"qch": [(s["uuid"], s["platform_status"], s["lifecycle"], s["Q"], s["T"], s["vkey"], s["created_at"]) for s in later],
                                         "raw_api": [(r["id"], r["status"], r.get("officialMetrics")) for r in raw_by_prefix(PAPER_LATER_LOW_Q[0])]},
    }
    # Q12/Q20 do not say "verified": report both populations explicitly
    def best_score(pop):
        ms = min(p["S"] for p in pop)
        return {"S": ms, "rows": [(p["uuid"][:7], p["vkey"], p["platform_status"], p["Q"], p["T"]) for p in pop if p["S"] == ms]}
    rev["q12_best_score_by_population"] = {"P1 accepted": best_score(accepted), "P5 evaluated": best_score(scored)}
    ref20 = by_prefix("8e9c9a2")[0]
    later20 = lambda pop: sorted((p["created_at"], p["uuid"][:7], p["vkey"], p["platform_status"], p["Q"], p["S"]) for p in pop  # noqa: E731
                                 if p["created_at"] > ref20["created_at"] and p["S"] < ref20["S"] and p["Q"] < ref20["Q"])
    rev["q20_later_better_by_population"] = {"reference": (ref20["uuid"][:7], ref20["vkey"], ref20["Q"], ref20["S"], ref20["created_at"]),
                                            "P1 accepted": later20(accepted), "P5 evaluated": later20(scored),
                                            "P5 evaluated with a CircuitVersion": [x for x in later20(scored) if x[2]]}
    checks["lowest Q among QCH accepted submissions is 1150 (3493c83 / ecdsafail:31f9c58)"] = rev["lowest_width_by_population"]["P1 accepted, all time"]["Q"] == 1150
    checks["b4a51d2 (Q=813) is in QCH as a rejected VALIDATED submission with version ecdsafail:364b042"] = bool(later and later[0]["Q"] == 813 and later[0]["platform_status"] == "rejected" and later[0]["vkey"] == "ecdsafail:364b042")
    checks["1150 is NOT the lowest width among evaluated submissions (P5 minimum < 813)"] = rev["lowest_width_by_population"]["P5 evaluated (accepted + rejected-with-metrics), all time"]["Q"] < 813

    # 4. Q14: branches and ancestry
    q14 = {"explicit_branch_labels": rows(con, "SELECT tag, count(*) AS n FROM version_tag GROUP BY 1"),
           "qch_parent_sha_resolvable_in_store": rows(con, "SELECT sum(parent_commit_sha IN (SELECT commit_sha FROM source_commit)) AS resolvable, count(*) AS n FROM source_commit")[0],
           "git_submission_branches": len([b for b in (git("branch", "-a", "--list", "origin/submissions/*") or "").splitlines() if b.strip()])}
    proxies = []
    best = next(s for s in accepted if s["S"] == min(a["S"] for a in accepted))
    candidates = [("lowest-Q accepted (proxy)", next(s for s in accepted if s["Q"] == 1150 and s["vkey"])), ("paper cutoff low-Q b6f2b0a", by_prefix("b6f2b0a")[0]), ("paper later low-Q b4a51d2", later[0])]
    for label, other in candidates:
        a, b = best["submission_commit_sha"], other["submission_commit_sha"]
        mb = (git("merge-base", "--all", a, b) or "").split()
        proxies.append({"pair": f"best-S accepted {best['uuid'][:7]} ({best['vkey']}) vs {label} {other['uuid'][:7]}",
                        "git_merge_base": [(x[:10], (git("log", "-1", "--format=%s", x) or "")[:60], git_ok("merge-base", "--is-ancestor", x, "origin/main")) for x in mb],
                        "commits_from_merge_base": [(git("rev-list", "--count", f"{x}..{a}"), git("rev-list", "--count", f"{x}..{b}")) for x in mb]})
    q14["git_ancestry_proxies"] = proxies
    rev["q14"] = q14
    checks["Q14: no explicit branch membership in QCH (only 'milestone' tags)"] = [t["tag"] for t in q14["explicit_branch_labels"]] == ["milestone"]

    # 5. Q18 interpretations
    rev["q18"] = {
        "(i) failed correctness/ancilla/phase with resource improvement": "no per-check outcome stored anywhere (QCH tables, raw API fields); cannot be evaluated",
        "(ii) platform rejection despite improvement over a branched_from parent": "see questions.Q18 (1 row: 449c44c < cb56b71)",
        "(iii) platform 'failed' status": rows(con, "SELECT count(*) AS n, sum(official_score IS NOT NULL) AS with_score, sum(official_peak_qubits IS NOT NULL) AS with_Q FROM submission_evaluation WHERE platform_status = 'failed'")[0],
        "failed rejection_reason classes": rows(con, "SELECT substr(rejection_reason, 1, 60) AS reason, count(*) AS n FROM submission_evaluation WHERE platform_status = 'failed' GROUP BY 1 ORDER BY 2 DESC"),
        "scored rejected reasons": rows(con, "SELECT rejection_reason, count(*) AS n FROM submission_evaluation WHERE platform_status = 'rejected' AND official_score IS NOT NULL GROUP BY 1"),
    }
    checks["Q18: every platform 'failed' evaluation lacks Q/T/S"] = rev["q18"]["(iii) platform 'failed' status"]["with_score"] == 0
    checks["Q18: all scored rejections have reason 'score did not improve current best'"] = [r["rejection_reason"] for r in rev["q18"]["scored rejected reasons"]] == ["score did not improve current best"]

    # 6. evaluator reference provenance
    ref = con.execute("SELECT benchmark_source_ref FROM evaluation_snapshot").fetchone()[0]
    rev["benchmark_source_ref"] = {"value": ref, "git_subject": git("log", "-1", "--format=%cI %s", ref), "is_qch_version": [r["external_version_key"] for r in rows(con, "SELECT external_version_key FROM circuit_version WHERE external_version_key = ?", "ecdsafail:" + ref[:7])],
                                   "per_version_link_columns": [c[1] for t in ("circuit_version", "submission_evaluation", "benchmark_run") for c in con.execute(f"PRAGMA table_info({t})") if "evaluator" in c[1] or c[1] == "benchmark_version"]}
    checks["benchmark_source_ref is one snapshot-wide challenge-repo commit (the best version's own commit), not a per-version evaluator version"] = bool(rev["benchmark_source_ref"]["is_qch_version"])

    # 7. coverage qualifiers for Q1/Q2
    rev["coverage_qualifiers"] = {
        "Q1 versions without commit time (QCH)": [r["version_label"] for r in rows(con, "SELECT version_label FROM circuit_version WHERE historical_time IS NULL ORDER BY sequence_no")],
        "Q1 V1-V5 commit times in external git": {k: git("log", "-1", "--format=%cI", k) for k in ("6f7c159", "d19dbb5", "cddd5df", "422f21d", "a39e07e")},
        "Q2 versions without official S (by lifecycle/platform)": rows(con, "SELECT s.status AS lifecycle, coalesce(e.platform_status, '(no evaluation)') AS platform, count(*) AS n FROM circuit_version v JOIN submission s ON s.submission_id = v.realized_from_submission_id LEFT JOIN submission_evaluation e ON e.submission_id = s.submission_id WHERE e.official_score IS NULL GROUP BY 1, 2"),
        "Q2 scored submissions without CircuitVersion": rows(con, "SELECT e.platform_status, count(*) AS n FROM submission s JOIN submission_evaluation e USING (submission_id) LEFT JOIN circuit_version v ON v.realized_from_submission_id = s.submission_id WHERE e.official_score IS NOT NULL AND v.version_id IS NULL GROUP BY 1"),
    }
    n_unscored = sum(r["n"] for r in rev["coverage_qualifiers"]["Q2 versions without official S (by lifecycle/platform)"])
    checks["Q1: exactly V1-V5 lack commit time in QCH"] = rev["coverage_qualifiers"]["Q1 versions without commit time (QCH)"] == ["V1", "V2", "V3", "V4", "V5"]
    checks["Q2: 51 versions lack official S; 730 = 855 scored submissions - 125 without CircuitVersion"] = n_unscored == 51 and 855 - sum(r["n"] for r in rev["coverage_qualifiers"]["Q2 scored submissions without CircuitVersion"]) == 730
    con.close()
    return rev, checks


if __name__ == "__main__":
    main()
