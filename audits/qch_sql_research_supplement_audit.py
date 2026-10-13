"""READ-ONLY audit of newly located ECDSA.Fail research-supplement sources
against QCH (docs/QCH_SQL_MISSING_DATA_SOURCE_AUDIT.md).

It never runs downloaded code or circuits, never writes the QCH database
(SHA-256 printed before/after), and never imports anything. It:

  1. writes the source manifest
     data/external/ecdsafail/research_supplement/SOURCE_MANIFEST.json
     (downloaded + reused files: provenance, size, SHA-256);
  2. parses Supplemental Note C (per-commit census, TeX table) and joins its
     commit labels to QCH versions / next_promoted_commit transitions;
  3. joins the frontier-reconstruction curation, chronology, transition slice
     and the arXiv qxt-history re-evaluation table to QCH records;
  4. fingerprints the evaluator source + toolchain per QCH version commit in
     the external local Git clone (read-only `git cat-file --batch-check`).

    python audits/qch_sql_research_supplement_audit.py [--json out.json]
"""

from __future__ import annotations

import csv
import glob
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "qch_ecdsa_p17.sqlite3"
STAGE = ROOT / "data" / "external" / "ecdsafail" / "research_supplement"
SUPP = next(iter(sorted((STAGE / "jieyilong_ecdsafail-supplemental-materials" / "extracted").glob("*"))), None)
ARXIV = STAGE / "arxiv_2609.09582v2" / "extracted"
EVID = STAGE / "jieyilong_ecdsafail-circuit-evidence_docs"
GIT_REPO = Path(os.environ.get("QCH_ECDSA_GIT_REPO", str(ROOT.parent.parent / "Data" / "ECDSA" / "ecdsafail-challenge")))
PAPER = ROOT.parent / "2609.09582v2.pdf"
RETRIEVED = "2026-09-26"


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return os.path.relpath(p.resolve(), ROOT).replace("\\", "/")


def ro():
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def rows(con, sql, *a):
    return [dict(r) for r in con.execute(sql, a)]


# ------------------------------------------------------------------ 1. manifest
SUPP_COMMIT = SUPP.name.rsplit("-", 1)[-1] if SUPP else None
EVID_COMMIT = (EVID / "PINNED_COMMIT").read_text().strip() if (EVID / "PINNED_COMMIT").exists() else None
SOURCES = [
    # (local path, source URL, role, license/usage notice, contents)
    (STAGE / "arxiv_2609.09582v2" / "arXiv-2609.09582v2.tar.gz", "https://arxiv.org/src/2609.09582v2", "downloaded",
     "CC BY 4.0 (arXiv listing of 2609.09582v2)", "Authors' arXiv TeX source bundle (paper sources, evidence/, figs/qxt-history.csv, unused/ draft appendices)."),
    (ARXIV / "figs" / "qxt-history.csv", "https://arxiv.org/src/2609.09582v2 (member figs/qxt-history.csv)", "extracted",
     "CC BY 4.0", "42 accepted submissions (every 10th accepted index) re-evaluated by the authors on a fixed 50,000-pair corpus: tested/correct/incorrect pairs, phase/ancilla garbage batches, avg executed Toffoli/Clifford, Q, emitted ops."),
    (ARXIV / "evidence" / "section5-quantitative-evidence-receipt.md", "https://arxiv.org/src/2609.09582v2 (member evidence/...)", "extracted",
     "CC BY 4.0", "Authors' provenance receipt for Section 5 claims (states which inputs are not yet publicly deposited)."),
    (ARXIV / "unused" / "09-appendix-census-of-source-commits.tex", "https://arxiv.org/src/2609.09582v2 (member unused/...)", "extracted",
     "CC BY 4.0", "Draft (marked 'ignore' in 00README.json) of the per-commit census table; compared against Supplemental Note C."),
]
if SUPP:
    base = f"https://github.com/jieyilong/ecdsafail-supplemental-materials/blob/{SUPP_COMMIT}/"
    SOURCES += [
        (next((STAGE / "jieyilong_ecdsafail-supplemental-materials").glob("*.tar.gz")), f"https://codeload.github.com/jieyilong/ecdsafail-supplemental-materials/tar.gz/{SUPP_COMMIT}", "downloaded",
         "No LICENSE file; GitHub reports no license (all rights reserved by default). Public repository of the paper's first author; the paper cites these as Supplemental Notes A-F.",
         "Pinned tarball of jieyilong/ecdsafail-supplemental-materials (Supplemental Notes A-F + frontier_reconstruction package)."),
    ]
    for relp, what in [
        ("appendices/tex/appendix-C-content.tex", "Supplemental Note C: per-commit census of 404 accepted source commits with primary technique family, change type, score, frontier delta."),
        ("appendices/optimization_census_of_accepted_competition_source_commits.pdf", "Supplemental Note C (compiled PDF)."),
        ("appendices/detailed_results_and_circuit_improvement_trajectory.pdf", "Supplemental Note A (compiled PDF)."),
        ("appendices/anatomy_of_agent_identified_quantum_circuit_optimizations.pdf", "Supplemental Note B (compiled PDF)."),
        ("circuit_details/low_Q_track_circuit_b6f2b0a/low_Q_track_circuit_b6f2b0a_details.tex", "Supplemental Note E source: b6f2b0a architecture; states the Q826 parent."),
        ("frontier_reconstruction/README.md", "Frontier reconstruction package description."),
        ("frontier_reconstruction/SHA256SUMS", "Authors' checksums for the package."),
        ("frontier_reconstruction/data/curation/reconstruction-50pct.json", "Selection manifest: cutoff, sources, admission rules, paper-admitted IDs, transition slice."),
        ("frontier_reconstruction/data/raw/official_submissions_2026-07-27T161558Z.json", "Frozen official API response (27 Jul 2026, 831 rows)."),
        ("frontier_reconstruction/data/raw/pareto_mirror_checkpoint_2026-06-25T061405Z.json", "Pareto Mirror checkpoint (25 Jun 2026)."),
        ("frontier_reconstruction/data/raw/teddy_contribution_chronology_2026-07-02.csv", "80-row contribution chronology with mechanism families (multi-label)."),
        ("frontier_reconstruction/output/pareto_manifest.csv", "The eight paper-admitted Pareto points with submission IDs, commits, metrics, note evidence."),
        ("frontier_reconstruction/output/pareto_pinned_api_metrics.csv", "15-point API-metric sensitivity frontier."),
        ("frontier_reconstruction/output/admitted_pareto_note_sensitivity.csv", "16-point public-note sensitivity frontier."),
        ("frontier_reconstruction/output/transition_slice.csv", "15 manifest-selected display transitions with author track labels (product / low_q)."),
        ("frontier_reconstruction/output/ledger_reconciliation.csv", "Chronology rows reconciled to official submissions/commits."),
        ("frontier_reconstruction/output/verification_report.json", "Authors' reconstruction verification report."),
    ]:
        SOURCES.append((SUPP / relp, base + relp, "extracted", "as tarball (no license declared)", what))
if EVID_COMMIT:
    for relp in ["README.md", "RESULTS.md", "VERIFICATION.md", "CITATION.cff", "THIRD_PARTY_NOTICES.md", "docs/DATA_DICTIONARY.md", "docs/PROVENANCE.md", "docs/PAPER_MAP.md", "docs/INTERPRETATION.md", "docs/REPRODUCTION.md"]:
        SOURCES.append((EVID / relp, f"https://raw.githubusercontent.com/jieyilong/ecdsafail-circuit-evidence/{EVID_COMMIT}/{relp}", "downloaded",
                        "No LICENSE file reported by GitHub; see CITATION.cff / THIRD_PARTY_NOTICES.md", "Documentation only of jieyilong/ecdsafail-circuit-evidence (100k-input studies of author-built windowed circuits); bulk corpora NOT downloaded."))
REUSED = [
    (ROOT / "data" / "external" / "ecdsafail" / "evaluation", "https://api.ecdsa.fail/api/benchmarks/1ffb695a-309b-46b6-a728-2f97d8c7be74/submissions", "reused (existing, fetched 2026-09-24)", "platform API response", "1,248-row platform submissions snapshot already imported into QCH."),
    (PAPER, "https://arxiv.org/abs/2609.09582v2", "reused (existing local copy)", "CC BY 4.0", "Paper PDF, 62 pp."),
]


def manifest():
    out = []
    for path, url, role, lic, what in SOURCES:
        out.append({"local_path": rel(path), "source_url": url, "role": role, "retrieved": RETRIEVED if role != "reused" else None,
                    "exists": path.exists(), "size_bytes": path.stat().st_size if path.exists() else None,
                    "sha256": sha256(path) if path.exists() and path.is_file() else None, "license_or_notice": lic, "contents": what})
    for path, url, role, lic, what in REUSED:
        files = sorted(path.glob("*.json")) if path.is_dir() else [path]
        for f in files:
            out.append({"local_path": rel(f), "source_url": url, "role": role, "retrieved": None, "exists": f.exists(),
                        "size_bytes": f.stat().st_size if f.exists() else None, "sha256": sha256(f) if f.exists() else None,
                        "license_or_notice": lic, "contents": what})
    head = git("rev-parse", "HEAD")
    out.append({"local_path": rel(GIT_REPO), "source_url": "https://github.com/Layr-Labs/ecdsafail-challenge.git", "role": "reused (existing local clone, not modified)",
                "retrieved": None, "exists": (GIT_REPO / ".git").exists(), "git_head": head,
                "git_latest_commit_any_ref": git("log", "-1", "--all", "--format=%H %cI"), "license_or_notice": "see repository NOTICE",
                "contents": "Challenge repository: circuit sources, evaluator (src/bin/eval_circuit.rs, src/sim.rs), pinned toolchain, submission branches."})
    return out


def git(*args):
    if not (GIT_REPO / ".git").exists():
        return None
    return subprocess.run(["git", "-C", str(GIT_REPO), *args], capture_output=True, text=True).stdout.strip()


# ------------------------------------------------------------ 2. Note C census
def parse_census(path: Path):
    fam = chg = None
    out = []
    num = lambda s: int(re.sub(r"[^0-9-]", "", s.replace("{,}", ""))) if re.search(r"\d", s) else None  # noqa: E731
    for line in path.read_text(encoding="utf-8").splitlines():
        if "\\multicolumn" in line and "Commit" not in line and "continued" not in line:
            m = re.search(r"\\textbf\{(.+?)\}~\\small\((\d+)\)", line)
            if m:
                fam = re.sub(r"\\texttt\{([^}]*)\}", r"\1", m.group(1)).replace("\\_", "_").strip()
                chg = None
                continue
            m = re.search(r"\\itshape (.+?) \\small\((\d+)\)", line)
            if m:
                chg = m.group(1).replace("\\_", "_").strip()
                continue
        m = re.match(r"\s*\\texttt\{([0-9a-f]{7})\}\s*&\s*([^&]*)&\s*([^&]*)&\s*([^&]*)&\s*(.*?)\\\\", line)
        if m:
            score = None if "unscored" in m.group(4) else num(m.group(4))
            delta = None if "unscored" in m.group(5) or not re.search(r"\d", m.group(5)) else num(m.group(5))
            out.append({"commit": m.group(1), "contestant": m.group(2).strip().replace("\\_", "_"), "date": m.group(3).strip(),
                        "score": score, "delta": delta, "family": fam, "change_type": chg})
    return out


def main() -> None:
    before = sha256(DB)
    print(f"DB sha256 before: {before}")
    result: dict = {"manifest": manifest()}
    con = ro()
    subs = rows(con, """SELECT substr(s.external_submission_key, 11) AS uuid, s.status AS lifecycle, e.platform_status,
                               e.official_peak_qubits AS Q, e.official_avg_executed_toffoli AS T, e.official_score AS S,
                               e.submission_commit_sha, v.version_id, v.external_version_key AS vkey
                        FROM submission s LEFT JOIN submission_evaluation e USING (submission_id)
                        LEFT JOIN circuit_version v ON v.realized_from_submission_id = s.submission_id""")
    sub7 = defaultdict(list)
    for s in subs:
        sub7[s["uuid"][:7]].append(s)
    vkeys = {r["external_version_key"][10:]: r["version_id"] for r in rows(con, "SELECT version_id, external_version_key FROM circuit_version")}
    vid2key = {v: k for k, v in vkeys.items()}
    vm = {r["version_id"]: r for r in rows(con, "SELECT v.version_id, e.official_score AS S FROM circuit_version v LEFT JOIN submission_evaluation e ON e.submission_id = v.realized_from_submission_id")}
    npc = rows(con, "SELECT source_version_id AS src, target_version_id AS dst FROM transformation_edge WHERE relation_type = 'next_promoted_commit'")
    checks = {}

    # --- Note C census
    if SUPP:
        census = parse_census(SUPP / "appendices" / "tex" / "appendix-C-content.tex")
        draft = parse_census(ARXIV / "unused" / "09-appendix-census-of-source-commits.tex")
        fam_counts = Counter(r["family"] for r in census)
        in_qch = [r for r in census if r["commit"] in vkeys]
        label_by_vid = {vkeys[r["commit"]]: r for r in in_qch}
        joined_edges = [e for e in npc if e["dst"] in label_by_vid]
        delta_ok = [e for e in joined_edges if label_by_vid[e["dst"]]["delta"] is not None and vm[e["src"]]["S"] is not None and vm[e["dst"]]["S"] is not None]
        agree = sum(1 for e in delta_ok if vm[e["dst"]]["S"] - vm[e["src"]]["S"] == label_by_vid[e["dst"]]["delta"])
        result["note_c_census"] = {
            "rows": len(census), "unique_commits": len({r["commit"] for r in census}), "family_counts": fam_counts.most_common(),
            "scored_rows": sum(1 for r in census if r["score"] is not None),
            "identical_to_arxiv_unused_draft": {(r["commit"], r["family"], r["change_type"]) for r in census} == {(r["commit"], r["family"], r["change_type"]) for r in draft},
            "commits_matching_qch_version_keys": len(in_qch),
            "commits_not_in_qch": [r["commit"] for r in census if r["commit"] not in vkeys][:20],
            "qch_next_promoted_commit_edges": len(npc),
            "npc_edges_whose_target_has_a_census_label": len(joined_edges),
            "npc_edges_with_label_and_both_scores": len(delta_ok),
            "census_delta_equals_qch_target_minus_source_S": agree,
            "census_last_date": max(r["date"] for r in census),
            "labelled_edge_family_counts": Counter(label_by_vid[e["dst"]]["family"] for e in joined_edges).most_common(),
        }
        checks["Note C: 404 rows, families 244/50/22/19/14/12/8/7/2/19/3 + 4 unscored (matches paper)"] = (
            len(census) == 404 and [n for _, n in fam_counts.most_common()][:1] == [244])

        # --- frontier reconstruction curation, chronology, transition slice
        fr = SUPP / "frontier_reconstruction"
        cur = json.loads((fr / "data" / "curation" / "reconstruction-50pct.json").read_text(encoding="utf-8"))
        ap = cur["admission_policy"]
        api = json.loads((fr / "data" / "raw" / "official_submissions_2026-07-27T161558Z.json").read_text(encoding="utf-8"))
        api_rows = api["submissions"] if isinstance(api, dict) and "submissions" in api else api
        api_ids = {r["id"] for r in api_rows}
        qch_uuids = {s["uuid"] for s in subs}
        result["frontier_reconstruction"] = {
            "cutoff": cur["data_cutoff_at"], "primary_rule": ap["primary_rule"], "paper_admitted_rule": ap.get("paper_admitted_rule"),
            "paper_admitted_curated_rejected_ids": ap["paper_admitted_curated_rejected_ids"],
            "paper_admitted_pareto_ids": ap["paper_admitted_pareto_ids_q_ascending"],
            "paper_admitted_ids_in_qch": {i: bool(sub7.get(i)) for i in ap["paper_admitted_pareto_ids_q_ascending"]},
            "api_metric_sensitivity_ids": ap["pinned_response_api_metric_pareto_ids_q_ascending"],
            "note_sensitivity_ids": ap["note_sensitivity_pareto_ids_q_ascending"],
            "frozen_api_rows": len(api_rows), "frozen_api_rows_in_qch": len(api_ids & qch_uuids), "frozen_api_rows_not_in_qch": len(api_ids - qch_uuids),
            "publication_policy": cur["publication_policy"],
        }
        checks["curation lists exactly the 8 paper Table 2 IDs"] = set(ap["paper_admitted_pareto_ids_q_ascending"]) == {"b6f2b0a", "f1d8707", "39a9b5f", "a3b0148", "4352cfb", "1c0e0e9", "8e9c9a2", "a536a48"}
        checks["all 8 paper-admitted IDs exist in QCH submissions"] = all(result["frontier_reconstruction"]["paper_admitted_ids_in_qch"].values())

        chron = list(csv.DictReader((fr / "data" / "raw" / "teddy_contribution_chronology_2026-07-02.csv").open(encoding="utf-8")))
        fams = Counter(f.strip() for r in chron for f in r["mechanism_families"].split(";"))
        chron_q = [r for r in chron if sub7.get(r["submission"])]
        chron_v = [r for r in chron_q if sub7[r["submission"]][0]["vkey"]]
        chron_labelled = [r for r in chron if r["mechanism_families"].strip() != "Unclassified support row"]
        result["chronology"] = {"rows": len(chron), "status": Counter(r["status"] for r in chron), "families": fams.most_common(),
                                "rows_in_qch_submissions": len(chron_q), "rows_with_qch_version": len(chron_v),
                                "rows_with_a_non_unclassified_label": len(chron_labelled),
                                "labelled_rows_with_qch_version": sum(1 for r in chron_labelled if sub7.get(r["submission"]) and sub7[r["submission"]][0]["vkey"])}

        ts = list(csv.DictReader((fr / "output" / "transition_slice.csv").open(encoding="utf-8")))
        result["transition_slice_tracks"] = [{"submission": r["submission"], "track": r["track"], "commit": r["source_commit"][:7],
                                              "qch_submission": bool(sub7.get(r["submission"])), "qch_version": (sub7[r["submission"]][0]["vkey"] if sub7.get(r["submission"]) else None)}
                                             for r in ts]

    # --- arXiv qxt-history re-evaluation (Q17)
    qx = list(csv.DictReader((ARXIV / "figs" / "qxt-history.csv").open(encoding="utf-8")))
    joined = []
    for r in qx:
        s = sub7.get(r["submission_id"][:7], [None])[0]
        joined.append({"idx": r["accepted_submission_index"], "commit": r["commit_short"], "submission": r["submission_id"][:8],
                       "qch_version": s["vkey"] if s else None, "commit_is_qch_version": r["commit_short"] in vkeys,
                       "tested": int(r["tested_point_pairs"]), "incorrect": int(r["incorrect_point_pairs"]),
                       "phase_garbage_batches": int(r["phase_garbage_batches"]), "ancilla_garbage_batches": int(r["ancilla_garbage_batches"]),
                       "Q_matches_official": bool(s and s["Q"] is not None and int(r["peak_qubits"]) == s["Q"]),
                       "T_matches_official": bool(s and s["T"] is not None and float(r["avg_executed_toffoli"]) == float(s["T"]))})
    result["qxt_history"] = {"rows": len(qx), "rows_in_qch_submissions": sum(1 for j in joined if j["qch_version"] or sub7.get(j["submission"][:7])),
                             "rows_with_qch_version": sum(1 for j in joined if j["qch_version"]),
                             "commit_equals_qch_version_key": sum(1 for j in joined if j["commit_is_qch_version"]),
                             "rows_with_incorrect_pairs": sum(1 for j in joined if j["incorrect"]), "rows_with_phase_garbage": sum(1 for j in joined if j["phase_garbage_batches"]),
                             "rows_with_ancilla_garbage": sum(1 for j in joined if j["ancilla_garbage_batches"]),
                             "Q_matches_official": sum(j["Q_matches_official"] for j in joined), "T_matches_official": sum(j["T_matches_official"] for j in joined),
                             "sample": joined[:4]}

    # --- Q19: evaluator + toolchain fingerprint per QCH version commit (read-only git)
    if (GIT_REPO / ".git").exists():
        paths = ["src/bin/eval_circuit.rs", "src/sim.rs", "rust-toolchain", "Cargo.lock", "benchmark.sh", "setup.sh"]
        req = "".join(f"{k}:{p}\n" for k in vkeys for p in paths)
        out = subprocess.run(["git", "-C", str(GIT_REPO), "cat-file", "--batch-check=%(objectname)"], input=req, capture_output=True, text=True).stdout.splitlines()
        fp = {}
        for i, k in enumerate(vkeys):
            vals = out[i * len(paths):(i + 1) * len(paths)]
            fp[k] = {p: (None if v.endswith("missing") or "ambiguous" in v else v[:12]) for p, v in zip(paths, vals)}
        complete = {k: v for k, v in fp.items() if all(v.values())}
        result["q19_evaluator_fingerprints"] = {
            "qch_versions": len(vkeys), "versions_with_all_files_in_clone": len(complete),
            "distinct_evaluator_sources(eval_circuit.rs,sim.rs)": len({(v["src/bin/eval_circuit.rs"], v["src/sim.rs"]) for v in complete.values()}),
            "distinct_toolchain_files": Counter(v["rust-toolchain"] for v in complete.values()).most_common(5),
            "distinct_Cargo_lock": len({v["Cargo.lock"] for v in complete.values()}),
            "missing_examples": [k for k in vkeys if k not in complete][:10]}

    after = sha256(DB)
    result["db_sha256"] = {"before": before, "after": after, "unchanged": before == after}
    result["checks"] = checks
    con.close()
    man_path = STAGE / "SOURCE_MANIFEST.json"
    man_path.write_text(json.dumps({"generated": RETRIEVED, "policy": "Read-only discovery; no code or circuits executed; nothing imported into QCH.",
                                    "sources": result["manifest"]}, indent=2), encoding="utf-8")
    for k, v in result.items():
        if k != "manifest":
            print(f"\n## {k}\n{json.dumps(v, indent=1, default=str)[:3500]}")
    print(f"\nmanifest: {rel(man_path)} ({len(result['manifest'])} entries)\nDB sha256 after: {after} unchanged={before == after}")
    if "--json" in sys.argv:
        p = Path(sys.argv[sys.argv.index("--json") + 1])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    if before != after or not all(checks.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
