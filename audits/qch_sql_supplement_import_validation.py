"""READ-ONLY validation of the schema-v6 research-supplement import
(docs/QCH_SQL_SUPPLEMENT_IMPORT_REPORT.md). Opens the production store and the
pre-import backup with `mode=ro` and never writes either.

Checks: SQLite integrity + foreign keys; every pre-existing table has the same
row count AND the same content digest as the backup; the new tables hold the
expected rows; the Note C delta check recomputed independently in SQL; and
the two evidence queries (paper-admitted members with Q/T; labelled
transitions with technique, versions and score delta).

    python audits/qch_sql_supplement_import_validation.py [--json results/qch_sql_audit/supplement_import_validation.json]
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "qch_ecdsa_p17.sqlite3"
BACKUP = ROOT / "data" / "backups" / "qch_ecdsa_p17.pre-supplement-import.758415f4a2df.sqlite3"
BACKUP_SHA256 = "758415f4a2df2fd6a74327faafcf4d790239fca97107265b4c10236a93603e7e"
NEW_TABLES = ["research_source", "version_technique_label", "research_population", "population_membership"]

MEMBERS_SQL = """
SELECT m.source_ref, substr(s.external_submission_key, 11) AS submission_uuid, m.member_role,
       e.platform_status, s.status AS lifecycle, v.external_version_key AS version,
       e.official_peak_qubits AS Q, e.official_avg_executed_toffoli AS T, e.official_score AS S
FROM population_membership m
JOIN research_population p ON p.population_id = m.population_id
JOIN submission s ON s.submission_id = m.submission_id
LEFT JOIN submission_evaluation e ON e.submission_id = s.submission_id
LEFT JOIN circuit_version v ON v.realized_from_submission_id = s.submission_id
WHERE p.name = 'paper_table2_pareto_points_2026_07_26'  -- v7: renamed from paper_admitted_2026_07_26
ORDER BY e.official_peak_qubits
"""

TRANSITIONS_SQL = """
SELECT l.source_row_number, l.family, l.change_type, src.external_version_key AS source_version,
       tgt.external_version_key AS target_version, es.official_score AS S_source, et.official_score AS S_target,
       et.official_score - es.official_score AS qch_delta, l.source_frontier_delta AS ledger_delta
FROM version_technique_label l
JOIN transformation_edge t ON t.edge_id = l.incoming_edge_id AND t.relation_type = 'next_promoted_commit'
JOIN circuit_version src ON src.version_id = t.source_version_id
JOIN circuit_version tgt ON tgt.version_id = t.target_version_id
LEFT JOIN submission_evaluation es ON es.submission_id = src.realized_from_submission_id
LEFT JOIN submission_evaluation et ON et.submission_id = tgt.realized_from_submission_id
ORDER BY tgt.historical_time
"""


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def ro(p: Path):
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def table_digest(con, table):
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    h = hashlib.sha256()
    n = 0
    for row in con.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY {', '.join(cols)}"):
        h.update(json.dumps(list(row), default=str).encode())
        n += 1
    return n, h.hexdigest()


def main() -> None:
    db_before = sha(DB)
    out: dict = {"db_sha256": db_before, "backup": {"path": BACKUP.relative_to(ROOT).as_posix(), "sha256": sha(BACKUP)}}
    out["backup"]["matches_recorded_sha256"] = out["backup"]["sha256"] == BACKUP_SHA256
    prod, back = ro(DB), ro(BACKUP)
    existing = [r[0] for r in back.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    out["integrity_check"] = prod.execute("PRAGMA integrity_check").fetchone()[0]
    out["foreign_key_violations"] = len(prod.execute("PRAGMA foreign_key_check").fetchall())
    out["migrations"] = [r[0] for r in prod.execute("SELECT version FROM schema_migrations ORDER BY version")]
    diffs = {}
    for t in existing:
        if t == "schema_migrations":
            continue
        a, b = table_digest(back, t), table_digest(prod, t)
        diffs[t] = {"rows": b[0], "unchanged": a == b}
    out["existing_tables"] = diffs
    out["existing_tables_all_unchanged"] = all(d["unchanged"] for d in diffs.values())
    out["new_table_counts"] = {t: prod.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in NEW_TABLES}
    out["sources"] = [dict(r) for r in prod.execute("SELECT source_kind, repository_url, repository_commit, file_path, sha256, retrieved_at, importer_version FROM research_source ORDER BY source_kind")]
    out["label_summary"] = {
        "labels": prod.execute("SELECT count(*) FROM version_technique_label").fetchone()[0],
        "distinct_versions": prod.execute("SELECT count(DISTINCT version_id) FROM version_technique_label").fetchone()[0],
        "with_incoming_edge": prod.execute("SELECT count(*) FROM version_technique_label WHERE incoming_edge_id IS NOT NULL").fetchone()[0],
        "unscored": [dict(r) for r in prod.execute("SELECT source_commit_ref, family, change_type, source_score, source_frontier_delta, resolved_commit_sha FROM version_technique_label WHERE source_scored = 0 ORDER BY source_row_number")],
        "with_full_commit_sha": prod.execute("SELECT count(*) FROM version_technique_label WHERE resolved_commit_sha IS NOT NULL").fetchone()[0],
        "family_counts": [tuple(r) for r in prod.execute("SELECT family, count(*) FROM version_technique_label GROUP BY family ORDER BY 2 DESC, 1")],
    }
    trans = [dict(r) for r in prod.execute(TRANSITIONS_SQL)]
    comparable = [t for t in trans if t["ledger_delta"] is not None and t["S_source"] is not None and t["S_target"] is not None]
    out["transitions"] = {"labelled_next_promoted_commit_edges": len(trans), "npc_edges_total": prod.execute("SELECT count(*) FROM transformation_edge WHERE relation_type='next_promoted_commit'").fetchone()[0],
                          "comparable": len(comparable), "ledger_delta_equals_qch_delta": sum(t["ledger_delta"] == t["qch_delta"] for t in comparable),
                          "mismatches": [t for t in comparable if t["ledger_delta"] != t["qch_delta"]],
                          "sample_evidence_rows": trans[:5] + trans[-3:]}
    out["paper_admitted_members"] = [dict(r) for r in prod.execute(MEMBERS_SQL)]
    out["population"] = [dict(r) for r in prod.execute("SELECT p.name, p.member_scope, p.cutoff_at, p.rule_text, s.file_path AS source_file, s.sha256 AS source_sha256 FROM research_population p JOIN research_source s USING (source_id)")]
    prod.close()
    back.close()
    out["db_sha256_unchanged_during_validation"] = sha(DB) == db_before
    print(json.dumps(out, indent=1, default=str))
    if "--json" in sys.argv:
        p = Path(sys.argv[sys.argv.index("--json") + 1])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    ok = (out["integrity_check"] == "ok" and out["foreign_key_violations"] == 0 and out["existing_tables_all_unchanged"] and out["backup"]["matches_recorded_sha256"]
          and out["new_table_counts"] == {"research_source": 2, "version_technique_label": 404, "research_population": 2, "population_membership": 422}  # v7: + 414-row candidate set
          and out["transitions"]["labelled_next_promoted_commit_edges"] == 403 and out["transitions"]["comparable"] == 399
          and out["transitions"]["ledger_delta_equals_qch_delta"] == 399 and len(out["paper_admitted_members"]) == 8 and out["db_sha256_unchanged_during_validation"])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
