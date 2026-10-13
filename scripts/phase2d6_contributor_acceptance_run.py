"""QCH Phase 2D.6: DETERMINISTIC real-data acceptance for contributor
provenance (spec sections 23 and 24) -- no LLM. Every query runs through
the ordinary QCHQueryExecutor with a compositional plan; expected values
are computed independently from the RAW imported snapshot (SHA-256
verified) and the public hub API (no SQL).

Case subjects come from data properties (most submissions, exactly one
submission, submissions but no CircuitVersion) rather than a fixed list;
the only named cases are the ones the spec names (the verified
welttowelt -> odinfree rename, and the unresolved paper name "Alan Li").
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from phase2d5_evaluation_acceptance_run import _raw_records  # noqa: E402
from qch import QCH  # noqa: E402
from qch.nl.answer import render_deterministic_answer  # noqa: E402
from qch.query.executor import QCHQueryExecutor  # noqa: E402
from qch.query.models import QCHQueryPlan, QCHQueryStatus  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"


def main() -> None:
    results: list[dict] = []
    with QCH.open(str(DB_PATH)) as hub:
        ex = QCHQueryExecutor(hub)
        raw = _raw_records(hub)
        qch_uuids = {s.external_submission_key.split(":", 1)[1] for s in hub.submissions.list() if s.external_submission_key}

        def run(plan):
            return ex.execute(QCHQueryPlan.from_dict(plan))

        def expected_uuids(handle):
            return sorted(u for u, r in raw.items() if r["solverUsername"] == handle and u in qch_uuids)

        def record(case, question, plan, result, expected, met, extra=None):
            results.append({
                "case": case, "question": question, "plan": plan, "status": result.status.value,
                "answer": render_deterministic_answer(question, result), "expected": expected, "expected_met": bool(met), **(extra or {}),
            })
            print(f"[{'PASS' if met else 'FAIL'}] {case}: {question} -> {result.status.value}")

        snapshot_id = hub.contributors.list_imports()[-1].snapshot_id
        everyone = run({"operation": "list_contributors"}).data
        by_count = sorted(everyone, key=lambda r: (-r["submission_count"], r["current_handle"].lower()))

        def contributor_case(case, handle, question):
            one = run({"operation": "list_contributors", "contributor": handle})
            subs = run({"operation": "list_submissions", "contributor": handle})
            want = expected_uuids(one.data[0]["current_handle"]) if one.status == QCHQueryStatus.ANSWERABLE else None
            got = sorted(r["submission_uuid"] for r in subs.data) if subs.status == QCHQueryStatus.ANSWERABLE else None
            info = one.data[0] if one.data else {}
            met = one.status == QCHQueryStatus.ANSWERABLE and got == want
            record(case, question, {"operation": "list_submissions", "contributor": handle}, subs, {"submission_uuids": len(want or [])}, met, {
                "resolved_identity": info.get("contributor_identity_id"), "source_identity_key": info.get("source_identity_key"),
                "current_handle": info.get("current_handle"), "historical_handles": info.get("historical_handles"),
                "resolved_via": info.get("resolved_via"), "submission_count": info.get("submission_count"),
                "circuit_version_count": info.get("submissions_with_circuit_version"),
                "official_evaluation_count": info.get("submissions_with_official_evaluation"),
                "provenance": f"Contribution(SUBMITTER, AUTHORITATIVE, source field solverAccountId) from snapshot {snapshot_id[:12]}",
            })
            return one, subs

        # -- section 23 --------------------------------------------------------------
        contributor_case("23A_known_current_handle", "gnuchev", "Show me all submissions by gnuchev.")
        new_one, new_subs = contributor_case("23B_current_handle_of_renamed_account", "odinfree", "Show me all submissions by odinfree.")
        old_one, old_subs = contributor_case("23B_historical_alias", "welttowelt", "Show me all submissions by welttowelt.")
        same = new_one.data[0]["contributor_identity_id"] == old_one.data[0]["contributor_identity_id"] and new_subs.data == old_subs.data
        record("23B_alias_converges", "Do odinfree and welttowelt resolve to the same identity?", None, old_one, {"same_identity": True}, same)
        contributor_case("23C_many_submissions", by_count[0]["current_handle"], f"How many submissions did {by_count[0]['current_handle']} make?")
        single = next(r for r in sorted(everyone, key=lambda r: r["current_handle"].lower()) if r["submission_count"] == 1)
        contributor_case("23D_exactly_one_submission", single["current_handle"], f"Show all submissions by {single['current_handle']}.")
        no_cv = next(r for r in sorted(everyone, key=lambda r: r["current_handle"].lower()) if r["submission_count"] > 0 and r["submissions_with_circuit_version"] == 0)
        contributor_case("23E_submissions_without_circuit_version", no_cv["current_handle"], f"Which CircuitVersions came from submissions by {no_cv['current_handle']}?")
        contributor_case("23F_official_evaluation", "gopikannappan", "Show the official evaluation metrics of submissions by gopikannappan.")
        for case, ref in (("23G_unknown_contributor", "no-such-account-zz"), ("23H_unresolved_person_name", "Alan Li")):
            r = run({"operation": "list_submissions", "contributor": ref})
            record(case, f"Show me all submissions by {ref}.", {"operation": "list_submissions", "contributor": ref}, r,
                   {"status": "unknown_entity", "not": "zero submissions / guessed account"},
                   r.status == QCHQueryStatus.UNKNOWN_ENTITY and r.data is None and r.unresolved["outcome"] == "unknown")

        # -- section 24 --------------------------------------------------------------
        top = by_count[0]["current_handle"]
        count = run({"operation": "list_submissions", "contributor": top})
        record("24_3_high_volume_count", f"How many submissions did {top} make?", {"operation": "list_submissions", "contributor": top}, count,
               {"count": len(expected_uuids(top))}, len(count.data) == len(expected_uuids(top)))

        v5 = next(v for c in hub.circuits.list() for v in hub.versions.list(c.logical_circuit_id) if v.version_label == "V5")
        v5_uuid = hub.submissions.get(v5.realized_from_submission_id).external_submission_key.split(":", 1)[1]
        who = run({"operation": "list_contributors", "version": v5.external_version_key})
        owner = next(r for r in who.data if r["role"] == "SUBMITTER")
        record("24_4_who_submitted_promoted_version", f"Who submitted {v5.external_version_key}?", {"operation": "list_contributors", "version": v5.external_version_key}, who,
               {"current_handle": raw[v5_uuid]["solverUsername"]}, owner["current_handle"] == raw[v5_uuid]["solverUsername"] and owner["evidence_class"] == "AUTHORITATIVE",
               {"attribution_path": owner["attribution_path"]})

        plan = {"steps": [{"operator": "list_submissions", "params": {}},
                          {"operator": "filter", "params": {"conditions": [{"metric": "evaluation.peak_qubits", "operator": "<", "value": 1000}]}},
                          {"operator": "group_by", "params": {"key": "contributor.submitter", "agg": "count"}}]}
        grouped = run(plan)
        want = {}
        for u in qch_uuids:
            r = raw.get(u)
            if r and (r["officialMetrics"] or {}).get("qubits") is not None and r["officialMetrics"]["qubits"] < 1000:
                want[r["solverUsername"]] = want.get(r["solverUsername"], 0) + 1
        record("24_5_accounts_with_peak_below_1000", "Which contributor accounts submitted circuits with official peak qubits below 1000?", plan, grouped,
               {"accounts": len(want)}, {g["group"]: g["count"] for g in grouped.data} == want)

        per = sorted(want, key=lambda h: (-want[h], h.lower()))[0]
        plan = {"steps": [{"operator": "list_submissions", "params": {"contributor": per}},
                          {"operator": "filter", "params": {"conditions": [{"metric": "evaluation.peak_qubits", "operator": "<", "value": 1000}]}}]}
        r = run(plan)
        record("24_5b_one_contributor_peak_below_1000", f"Show submissions by {per} with official peak qubits below 1000.", plan, r, {"count": want[per]}, len(r.data) == want[per])

        plan = {"steps": [{"operator": "list_submissions", "params": {"contributor": top}}, {"operator": "sort", "params": {"by": "evaluation.score", "order": "asc"}}]}
        r = run(plan)
        scores = [x["evaluation.score"] for x in r.data if x.get("evaluation.score") is not None]
        best = min(raw[u]["officialScore"] for u in expected_uuids(top) if raw[u]["officialScore"] is not None)
        record("24_6_rank_by_official_score", f"Rank the submissions by {top} by official score.", plan, r, {"best_official_score": best}, scores == sorted(scores) and scores[0] == best)

        plan = {"steps": [{"operator": "list_submissions", "params": {"contributor": top}}, {"operator": "sort", "params": {"by": "platform.created_at", "order": "asc"}}]}
        r = run(plan)
        times = [x["platform.created_at"] for x in r.data]
        record("24_7_chronological", f"Show the submissions by {top} over time.", plan, r, {"first": min(raw[u]["createdAt"] for u in expected_uuids(top))}, times == sorted(times) and times[0] == min(raw[u]["createdAt"] for u in expected_uuids(top)))

        r = run({"operation": "list_submissions", "contributor": "gopikannappan"})
        with_cv = [x for x in r.data if x.get("external_version_key")]
        record("24_8_contributor_to_submission_to_version", "Which CircuitVersions came from submissions by gopikannappan?", {"operation": "list_submissions", "contributor": "gopikannappan"}, r,
               {"with_circuit_version": run({"operation": "list_contributors", "contributor": "gopikannappan"}).data[0]["submissions_with_circuit_version"]},
               len(with_cv) == run({"operation": "list_contributors", "contributor": "gopikannappan"}).data[0]["submissions_with_circuit_version"],
               {"example_path": f"ContributorIdentity gopikannappan -> Contribution(SUBMITTER, AUTHORITATIVE) -> Submission {with_cv[0]['submission_uuid']} -> realized_from_submission_id -> CircuitVersion {with_cv[0]['external_version_key']}" if with_cv else None})

        probe = sorted(x["external_version_key"] for x in with_cv)[0]
        r = run({"operation": "list_contributors", "version": probe})
        owner = next(x for x in r.data if x["role"] == "SUBMITTER")
        record("24_9_version_to_submission_to_contributor", f"Who submitted {probe}?", {"operation": "list_contributors", "version": probe}, r,
               {"current_handle": "gopikannappan"}, owner["current_handle"] == "gopikannappan", {"attribution_path": owner["attribution_path"]})

        r = run({"operation": "list_submissions", "contributor": "Alan Li"})
        record("24_10_alan_li", "Show me all submissions by Alan Li.", {"operation": "list_submissions", "contributor": "Alan Li"}, r,
               {"status": "unknown_entity (unresolved identity; NOT zero submissions, NOT a guessed account)"},
               r.status == QCHQueryStatus.UNKNOWN_ENTITY and r.data is None)

    out = PROJECT_ROOT / "results" / "phase2d6" / "contributor_acceptance_deterministic_2026-09-24.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"results": results}, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out} ({sum(r['expected_met'] for r in results)}/{len(results)} met)")


if __name__ == "__main__":
    main()
