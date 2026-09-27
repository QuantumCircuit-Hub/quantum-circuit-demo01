"""Independent correctness verification for Ask QCH example-question candidates.

Every expectation is recomputed from the store through deterministic hub APIs
(hub.submissions / hub.evaluations / hub.contributors / hub.versions /
hub.evolution / qch.query.metrics.unified_metrics) -- never from the Query
Engine operators or the NL layer, and never with raw SQL. Each candidate's
service result (from scripts/example_questions_benchmark.py) is then
classified VERIFIED_CORRECT / INCORRECT / PARTIAL / UNVERIFIABLE.

    python scripts/example_questions_verify.py <benchmark.json> [<more.json> ...] [--out out.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.query.metrics import unified_metrics  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"


def fmt(n) -> str:
    return f"{int(n):,}"


class Facts:
    """Read-only facts from hub APIs."""

    def __init__(self, hub):
        self.hub = hub
        self.subs = {s.external_submission_key.split(":", 1)[1]: s for s in hub.submissions.list()}
        self.evals = {u: hub.evaluations.latest(s.submission_id) for u, s in self.subs.items()}
        handles = {i.contributor_identity_id: i.current_handle for i in hub.contributors.list()}
        self.submitter = {}
        for cs in hub.contributors.latest_contributions().values():
            for c in cs:
                if c.role == "SUBMITTER" and c.contributor_identity_id:
                    self.submitter[c.source_submission_uuid] = handles[c.contributor_identity_id]
        (circuit,) = hub.circuits.list()
        self.versions = {v.external_version_key: v for v in hub.versions.list(circuit.logical_circuit_id)}
        self.by_vid = {v.version_id: v for v in self.versions.values()}
        self.version_of_sub = {v.realized_from_submission_id: k for k, v in self.versions.items() if v.realized_from_submission_id}

    def sub(self, prefix):
        (uuid,) = [u for u in self.subs if u.startswith(prefix)]
        return uuid

    def official(self, uuid):
        e = self.evals[uuid]
        return {"evaluation.peak_qubits": e.official_peak_qubits, "evaluation.avg_executed_toffoli": e.official_avg_executed_toffoli, "evaluation.score": e.official_score}

    def metrics(self, key):
        return unified_metrics(self.hub, self.versions[key].version_id)

    def edges(self, relation):
        for v in self.versions.values():
            for e in self.hub.evolution.list_edges_from(v.version_id):
                if e.relation_type == relation and e.target_version_id in self.by_vid:
                    yield v, self.by_vid[e.target_version_id]

    def select(self, *, score_lt=None, qubits_lt=None, platform=None, lifecycle=None, submitter=None, has_official=False):
        out = set()
        for u, s in self.subs.items():
            e = self.evals[u]
            if lifecycle and s.status != lifecycle:
                continue
            if platform and (e is None or e.platform_status != platform):
                continue
            if submitter and self.submitter.get(u) != submitter:
                continue
            if has_official and not (e and e.passed):
                continue
            if score_lt is not None and not (e and e.official_score is not None and e.official_score < score_lt):
                continue
            if qubits_lt is not None and not (e and e.official_peak_qubits is not None and e.official_peak_qubits < qubits_lt):
                continue
            out.add(u)
        return out


def _uuids(data):
    return {r.get("submission_uuid") for r in data} if isinstance(data, list) else None


def _keys(data, field="external_version_key"):
    return {r.get(field) for r in data} if isinstance(data, list) else None


def _all_in(answer, needles):
    missing = [n for n in needles if n not in (answer or "")]
    return ("VERIFIED_CORRECT", "all expected facts present") if not missing else ("INCORRECT", f"missing {missing}")


def _set_check(got, expected, what):
    if got is None:
        return "INCORRECT", f"no list result for {what}"
    if got == expected:
        return "VERIFIED_CORRECT", f"{what}: {len(expected)} match the independent set"
    return "INCORRECT", f"{what}: got {len(got)}, expected {len(expected)}; missing {sorted(expected - got)[:3]} extra {sorted(got - expected)[:3]}"


def _transition_set(f, relation, conditions):
    ops = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b}
    out = set()
    for p, c in f.edges(relation):
        a, b = unified_metrics(f.hub, p.version_id), unified_metrics(f.hub, c.version_id)
        ok = True
        for metric, kind, op, thr in conditions:
            x, y = a.get(metric), b.get(metric)
            if x is None or y is None or (kind == "pct" and x == 0):
                ok = False
                break
            val = (y - x) / x * 100 if kind == "pct" else y - x
            ok = ok and ops[op](val, thr)
        if ok:
            out.add((p.external_version_key, c.external_version_key))
    return out


def verify(cid, row, f: Facts):
    ans, data, status = row["answer"], row.get("raw_data"), row["status"]
    if status not in ("answered", "missing_data"):
        return ("UNVERIFIABLE" if status == "needs_clarification" else "INCORRECT"), f"status {status}"
    if cid == "B1":
        vals = {k: m["structural.toffoli_count"] for k in f.versions for m in [f.metrics(k)] if "structural.toffoli_count" in m}
        best = min(vals, key=vals.get)
        return _set_check(_keys(data), {best}, f"lowest structural Toffoli ({best}, {fmt(vals[best])})")
    if cid == "B2":
        deltas = {}
        for p, c in f.edges("next_promoted_commit"):
            a, b = f.metrics(p.external_version_key), f.metrics(c.external_version_key)
            if "structural.toffoli_count" in a and "structural.toffoli_count" in b:
                deltas[(p.external_version_key, c.external_version_key)] = b["structural.toffoli_count"] - a["structural.toffoli_count"]
        best = min(deltas, key=deltas.get)
        got = {(r["parent_external_key"], r["child_external_key"]) for r in data}
        return _set_check(got, {best}, f"largest structural Toffoli reduction {best} ({fmt(deltas[best])})")
    if cid == "B3":
        exp = {k for k in f.versions if f.metrics(k).get("structural.toffoli_count", 10**12) < 950000}
        return _set_check(_keys(data) or set(), exp, "structural Toffoli < 950000 (empty is correct)")
    if cid in ("B4", "C17"):
        parent = "ecdsafail:1196b9f" if cid == "B4" else "ecdsafail:c24c306"
        exp = {c.external_version_key for p, c in f.edges("branched_from") if p.external_version_key == parent}
        return _set_check(_keys(data), exp, f"branched from {parent}")
    if cid == "C1":
        u = f.sub("8e9c9a2")
        o = f.official(u)
        return _all_in(ans, [u, f.version_of_sub[f.subs[u].submission_id], f.submitter[u], fmt(o["evaluation.score"]), fmt(o["evaluation.peak_qubits"])])
    if cid in ("C2", "C31"):
        key = "ecdsafail:897dda2" if cid == "C2" else "ecdsafail:c24c306"
        v = f.versions[key]
        u = f.subs[[x for x, s in f.subs.items() if s.submission_id == v.realized_from_submission_id][0]].external_submission_key.split(":", 1)[1]
        needles = [key, u, f.submitter[u]] + [fmt(val) for name, val in f.metrics(key).items() if name.startswith("structural.")]
        if cid == "C31":
            needles += sorted(c.external_version_key for p, c in f.edges("branched_from") if p.external_version_key == key)
        return _all_in(ans, needles)
    if cid in ("C3", "C4", "C5", "C21"):
        prefix, metrics = {"C3": ("8e9c9a2", ["evaluation.score"]), "C4": ("3616dbf", ["evaluation.peak_qubits", "evaluation.avg_executed_toffoli"]),
                           "C5": ("8e9c9a2", ["evaluation.score", "evaluation.peak_qubits", "evaluation.avg_executed_toffoli"]),
                           "C21": ("02103492", ["evaluation.peak_qubits", "evaluation.score"])}[cid]
        o = f.official(f.sub(prefix))
        return _all_in(ans, [fmt(o[m]) for m in metrics])
    if cid in ("C6", "C7"):
        m = f.metrics("ecdsafail:897dda2")
        names = ["structural.toffoli_count"] + (["structural.qubit_count"] if cid == "C7" else [])
        return _all_in(ans, [fmt(m[n]) for n in names])
    if cid in ("C8", "C22", "C26"):
        spec = {"C8": ("3616dbf", "8e9c9a2", "evaluation.score"), "C22": ("02103492", "8e9c9a2", "evaluation.peak_qubits"),
                "C26": ("ecdsafail:422f21d", "ecdsafail:a39e07e", "evaluation.score")}[cid]

        def val(ref):
            if ref.startswith("ecdsafail:"):
                sid = f.versions[ref].realized_from_submission_id
                ref = [u for u, s in f.subs.items() if s.submission_id == sid][0]
            return f.official(f.sub(ref))[spec[2]]
        a, b = val(spec[0]), val(spec[1])
        return _all_in(ans, [f"{fmt(a)} -> {fmt(b)}", fmt(abs(b - a))])
    if cid == "C9":
        a, b = f.metrics("ecdsafail:897dda2")["structural.toffoli_count"], f.metrics("ecdsafail:cae8c82")["structural.toffoli_count"]
        return _all_in(ans, [f"{fmt(a)} -> {fmt(b)}", fmt(abs(b - a))])
    selections = {
        "C10": dict(score_lt=1140000000), "C11": dict(qubits_lt=793), "C12": dict(platform="rejected", score_lt=1200000000),
        "C13": dict(submitter="odinfree"), "C15": dict(submitter="odinfree"), "C24": dict(lifecycle="PROMOTED", submitter="odinfree"),
        "C28": dict(platform="rejected", has_official=True), "C29": dict(submitter="Rossnkama"),
        "C30": dict(lifecycle="PROMOTED", submitter="odinfree", score_lt=1140000000), "C32": dict(lifecycle="PROMOTED", score_lt=1140000000),
        "C33": dict(lifecycle="PROMOTED", submitter="teddyjfpender", has_official=True),
    }
    if cid in selections:
        return _set_check(_uuids(data), f.select(**selections[cid]), f"submissions {selections[cid]}")
    if cid == "C16":
        v = f.versions["ecdsafail:37ab267"]
        u = [x for x, s in f.subs.items() if s.submission_id == v.realized_from_submission_id][0]
        return _all_in(ans, [u, f.submitter[u]])
    if cid == "C23":
        scored = {u: e.official_score for u, e in f.evals.items() if e and e.official_score}
        best = min(scored, key=scored.get)
        return _set_check(_uuids(data), {best}, f"lowest official score ({best}, {fmt(scored[best])})")
    transitions = {
        "C18": ("next_promoted_commit", [("structural.toffoli_count", "pct", "<", -1.0), ("structural.qubit_count", "delta", "<", 0)]),
        "C19": ("historical_successor", [("toffoli_count", "pct", "<", -40.0)]),
        "C20": ("next_promoted_commit", [("structural.toffoli_count", "delta", ">", 0), ("structural.qubit_count", "delta", "<", 0)]),
        "C25": ("next_promoted_commit", [("structural.qubit_count", "delta", "<", -10)]),
    }
    if cid in transitions:
        got = {(r["parent_external_key"], r["child_external_key"]) for r in data} if isinstance(data, list) else None
        return _set_check(got, _transition_set(f, *transitions[cid]), f"transitions {transitions[cid][1]}")
    if cid == "C27":
        m = f.metrics("ecdsafail:a39e07e")
        return _all_in(ans, ["ecdsafail:a39e07e", "3d03172f", "odinfree", fmt(m["toffoli_count"]), fmt(m["score"])])
    return "UNVERIFIABLE", "no oracle"


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--out=")), None)
    rows = [r for p in args for r in json.loads(Path(p).read_text(encoding="utf-8"))["results"]]
    verdicts = {}
    with QCH.open(str(DB_PATH)) as hub:
        f = Facts(hub)
        for r in rows:
            verdict, why = verify(r["id"], r, f)
            verdicts[r["id"]] = {"verdict": verdict, "reason": why}
            print(f"[{r['id']}] {verdict:<17} {why}")
    if out:
        Path(out).write_text(json.dumps(verdicts, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
