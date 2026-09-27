"""READ-ONLY validation of the schema-v7 population correction
(docs/QCH_SQL_SUPPLEMENT_IMPORT_REPORT.md, correction addendum). Opens the
production store and the pre-correction (schema-v6) backup with `mode=ro`
and never writes either. Independent of the importer: plain SQL + the
pinned raw API response read as data (no downloaded code is run).

Checks: integrity + foreign keys; migrations 1..7; every pre-existing table,
the 404 technique labels, the research sources and the eight frontier
membership rows are content-identical to the backup; the candidate set
re-derived (a) in SQL from QCH and (b) from the pinned response equals the
stored candidate membership; no rejected member outside the curated six;
the two-objective Pareto frontier computed in SQL over the stored candidate
set equals the eight listed Table 2 IDs and pareto_manifest.csv; no
population's rule text claims "all promoted" unless it is the candidate set;
the 399/399 Note C delta check.

    python audits/qch_sql_population_correction_validation.py [--json results/qch_sql_audit/population_correction_validation.json]
"""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "qch_ecdsa_p17.sqlite3"
BACKUP = ROOT / "data" / "backups" / "qch_ecdsa_p17.pre-population-correction.d7a4aaf8854f.sqlite3"
BACKUP_SHA256 = "d7a4aaf8854ffe8296f96b9a1130d6fed6f36baa8173c22fe0a4bbf33ad6ee15"
FR = (ROOT / "data" / "external" / "ecdsafail" / "research_supplement" / "jieyilong_ecdsafail-supplemental-materials" / "extracted"
      / "ecdsafail-supplemental-materials-ef7ecd2b27cf27881874468e003e53e16ba48075" / "frontier_reconstruction")
RAW = FR / "data" / "raw" / "official_submissions_2026-07-27T161558Z.json"
RAW_SHA256 = "915af8b7be4283dbd357d5e8ed013d91c494e81fcee6528aa3dc1385706aebec"
CURATION = FR / "data" / "curation" / "reconstruction-50pct.json"
CURATION_SHA256 = "56ec6b7a876dbb4a7aedd31324220f6c637224862d08f415197f39a1982a4009"
PARETO_MANIFEST = FR / "output" / "pareto_manifest.csv"
CANDIDATES = "paper_admitted_candidates_2026_07_26"
FRONTIER = "paper_table2_pareto_points_2026_07_26"
LEGACY = "paper_admitted_2026_07_26"

CANDIDATE_SQL = """
SELECT substr(s.external_submission_key, 11) AS uuid, e.official_peak_qubits AS q, e.official_avg_executed_toffoli AS t
FROM population_membership m
JOIN research_population p ON p.population_id = m.population_id
JOIN submission s ON s.submission_id = m.submission_id
JOIN submission_evaluation e ON e.submission_id = s.submission_id
WHERE p.name = :name
"""

FRONTIER_SQL = f"""
WITH c AS ({CANDIDATE_SQL})
SELECT substr(a.uuid, 1, 7) AS id, a.q, a.t FROM c a
WHERE NOT EXISTS (SELECT 1 FROM c b WHERE b.q <= a.q AND b.t <= a.t AND (b.q < a.q OR b.t < a.t))
ORDER BY a.q, a.t, a.uuid
"""

QCH_DERIVATION_SQL = """
SELECT substr(s.external_submission_key, 11) AS uuid
FROM submission_evaluation e JOIN submission s ON s.submission_id = e.submission_id
WHERE e.official_peak_qubits IS NOT NULL AND e.official_avg_executed_toffoli IS NOT NULL AND e.platform_created_at <= :cutoff
  AND (e.promotion_status = 'promoted'
       OR (e.platform_status = 'rejected' AND substr(s.external_submission_key, 11, 7) IN (SELECT value FROM json_each(:six))))
"""

DELTA_SQL = """
SELECT count(*) AS comparable, sum(l.source_frontier_delta = et.official_score - es.official_score) AS agree
FROM version_technique_label l
JOIN transformation_edge t ON t.edge_id = l.incoming_edge_id AND t.relation_type = 'next_promoted_commit'
JOIN circuit_version src ON src.version_id = t.source_version_id
JOIN circuit_version tgt ON tgt.version_id = t.target_version_id
JOIN submission_evaluation es ON es.submission_id = src.realized_from_submission_id
JOIN submission_evaluation et ON et.submission_id = tgt.realized_from_submission_id
WHERE l.source_frontier_delta IS NOT NULL AND es.official_score IS NOT NULL AND et.official_score IS NOT NULL
"""


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def ro(p: Path):
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def digest(con, sql, params=()):
    h, n = hashlib.sha256(), 0
    for row in con.execute(sql, params):
        h.update(json.dumps(list(row), default=str).encode())
        n += 1
    return n, h.hexdigest()


def table_digest(con, table, cols=None):
    cols = cols or [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    return digest(con, f"SELECT {', '.join(cols)} FROM {table} ORDER BY {', '.join(cols)}")


def main() -> None:
    db_before = sha(DB)
    out: dict = {"db_sha256": db_before, "backup": {"path": BACKUP.relative_to(ROOT).as_posix(), "sha256": sha(BACKUP)}}
    out["backup"]["matches_recorded_sha256"] = out["backup"]["sha256"] == BACKUP_SHA256
    raw_bytes, cur_bytes = RAW.read_bytes(), CURATION.read_bytes()
    out["pinned_inputs"] = {"raw_response_sha256_ok": hashlib.sha256(raw_bytes).hexdigest() == RAW_SHA256,
                            "curation_sha256_ok": hashlib.sha256(cur_bytes).hexdigest() == CURATION_SHA256}
    cur = json.loads(cur_bytes)
    cutoff = cur["data_cutoff_at"]
    six = cur["admission_policy"]["paper_admitted_curated_rejected_ids"]
    listed = cur["admission_policy"]["paper_admitted_pareto_ids_q_ascending"]
    manifest_ids = [r["submission"] for r in csv.DictReader(PARETO_MANIFEST.open(encoding="utf-8"))]
    prod, back = ro(DB), ro(BACKUP)

    out["integrity_check"] = prod.execute("PRAGMA integrity_check").fetchone()[0]
    out["foreign_key_violations"] = len(prod.execute("PRAGMA foreign_key_check").fetchall())
    out["migrations"] = [r[0] for r in prod.execute("SELECT version FROM schema_migrations ORDER BY version")]

    # unchanged content vs the schema-v6 backup
    unchanged = {}
    for t in [r[0] for r in back.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]:
        if t in ("schema_migrations", "research_population", "population_membership"):
            continue
        unchanged[t] = table_digest(back, t) == table_digest(prod, t)
    frontier_id = back.execute("SELECT population_id FROM research_population WHERE name = ?", (LEGACY,)).fetchone()[0]
    member_sql = "SELECT population_id, submission_id, source_ref, source_list_field, member_role FROM population_membership WHERE population_id = ? ORDER BY submission_id"
    unchanged["population_membership[frontier 8 rows]"] = digest(back, member_sql, (frontier_id,)) == digest(prod, member_sql, (frontier_id,))
    v6_cols = ["population_id", "source_id", "member_scope", "cutoff_at"]
    unchanged["research_population[frontier id/source/scope/cutoff]"] = (
        digest(back, f"SELECT {', '.join(v6_cols)} FROM research_population WHERE population_id = ?", (frontier_id,))
        == digest(prod, f"SELECT {', '.join(v6_cols)} FROM research_population WHERE population_id = ?", (frontier_id,)))
    out["unchanged_vs_backup"] = unchanged
    out["unchanged_all"] = all(unchanged.values())
    out["existing_table_row_counts"] = {t: prod.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in unchanged if "[" not in t}

    # populations
    pops = [dict(r) for r in prod.execute(
        "SELECT p.population_id, p.name, p.population_role, p.derived_from_population_id, p.rule_source_field, p.cutoff_at, p.rule_text, p.semantics,"
        " (SELECT count(*) FROM population_membership m WHERE m.population_id = p.population_id) AS members FROM research_population p ORDER BY p.name")]
    out["populations"] = pops
    by_name = {p["name"]: p for p in pops}
    out["legacy_name_absent"] = LEGACY not in by_name
    out["rule_text_consistency"] = all(("all promoted" not in p["rule_text"].lower()) or (p["population_role"] == "candidate_set" and p["members"] > 8) for p in pops)
    out["frontier_links_candidate"] = by_name[FRONTIER]["derived_from_population_id"] == by_name[CANDIDATES]["population_id"] and by_name[FRONTIER]["population_id"] == frontier_id

    # candidate set: stored vs (a) QCH SQL derivation vs (b) pinned response derivation
    stored = {r["uuid"] for r in prod.execute(CANDIDATE_SQL, {"name": CANDIDATES})}
    qch_derived = {r["uuid"] for r in prod.execute(QCH_DERIVATION_SQL, {"cutoff": cutoff, "six": json.dumps(six)})}
    rows = json.loads(raw_bytes)["submissions"]
    raw_derived = {r["id"] for r in rows if r["createdAt"] <= cutoff and (r.get("officialMetrics") or {}).get("qubits") and (r.get("officialMetrics") or {}).get("toffoli")
                   and (r.get("promotionStatus") == "promoted" or (r["status"] == "rejected" and r["id"][:7] in six))}
    not_promoted = sorted(r["uuid"][:7] for r in prod.execute(
        "SELECT substr(s.external_submission_key, 11) AS uuid FROM population_membership m JOIN research_population p USING (population_id)"
        " JOIN submission s ON s.submission_id = m.submission_id JOIN submission_evaluation e ON e.submission_id = s.submission_id"
        " WHERE p.name = ? AND coalesce(e.promotion_status, '') <> 'promoted'", (CANDIDATES,)))
    out["candidate_set"] = {"stored": len(stored), "qch_sql_derivation": len(qch_derived), "pinned_response_derivation": len(raw_derived),
                            "stored_equals_qch_derivation": stored == qch_derived, "stored_equals_pinned_response_derivation": stored == raw_derived,
                            "promoted": len(stored) - len(not_promoted), "not_promoted_members": not_promoted,
                            "not_promoted_equals_curated_six": set(not_promoted) == set(six),
                            "candidate_count_stated_in_source": None}

    # Pareto frontier in SQL over the stored candidate set
    frontier = [dict(r) for r in prod.execute(FRONTIER_SQL, {"name": CANDIDATES})]
    ids = [f["id"] for f in frontier]
    stored_frontier = sorted(r[0] for r in prod.execute(
        "SELECT m.source_ref FROM population_membership m JOIN research_population p USING (population_id) WHERE p.name = ?", (FRONTIER,)))
    out["pareto"] = {"candidate_count": len(stored), "frontier_count": len(ids), "frontier_ids_q_ascending": ids, "frontier_points": frontier,
                     "equals_curation_listed_ids": ids == listed, "equals_pareto_manifest": ids == manifest_ids,
                     "equals_stored_frontier_population": sorted(ids) == stored_frontier,
                     "duplicate_coordinates": prod.execute(f"WITH c AS ({CANDIDATE_SQL}) SELECT count(*) FROM (SELECT q, t FROM c GROUP BY q, t HAVING count(*) > 1)",
                                                           {"name": CANDIDATES}).fetchone()[0]}
    d = dict(prod.execute(DELTA_SQL).fetchone())
    out["technique_delta_check"] = {**d, "labels": prod.execute("SELECT count(*) FROM version_technique_label").fetchone()[0]}
    prod.close()
    back.close()
    out["db_sha256_unchanged_during_validation"] = sha(DB) == db_before
    ok = (out["backup"]["matches_recorded_sha256"] and all(out["pinned_inputs"].values()) and out["integrity_check"] == "ok"
          and out["foreign_key_violations"] == 0 and out["migrations"] == [1, 2, 3, 4, 5, 6, 7] and out["unchanged_all"]
          and out["legacy_name_absent"] and out["rule_text_consistency"] and out["frontier_links_candidate"]
          and out["candidate_set"]["stored_equals_qch_derivation"] and out["candidate_set"]["stored_equals_pinned_response_derivation"]
          and out["candidate_set"]["not_promoted_equals_curated_six"] and out["pareto"]["equals_curation_listed_ids"]
          and out["pareto"]["equals_pareto_manifest"] and out["pareto"]["equals_stored_frontier_population"]
          and (d["comparable"], d["agree"]) == (399, 399) and out["db_sha256_unchanged_during_validation"])
    out["all_checks_pass"] = ok
    print(json.dumps(out, indent=1, default=str))
    if "--json" in sys.argv:
        p = Path(sys.argv[sys.argv.index("--json") + 1])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
