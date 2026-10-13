"""READ-ONLY validation of the schema-v8 submission-details import
(docs/QCH_SUBMISSION_SOURCE_DETAILS_IMPORT.md). Opens the production store and
the pre-change backup with `mode=ro` and never writes either. Independent of
the importer: plain SQL plus the raw source bodies read as data.

Checks: integrity + foreign keys; migrations 1..8; every pre-existing table is
content-identical to the backup (schema_migrations: only row 8 added) and no
pre-existing schema object changed; every stored detail row equals its source
record field by field (note character-for-character, claimedScore value,
coauthors structure, timestamps, presence); the QCH link is the exact-UUID
match; the latest view has one row per platform submission from the most
recently fetched body; the required example submission.

    python audits/qch_sql_submission_details_validation.py [--json results/submission_details/validation.json]
"""

from __future__ import annotations

import collections
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "qch_ecdsa_p17.sqlite3"
BACKUP = ROOT / "data" / "backups" / "qch_ecdsa_p17.pre-submission-details.7f8349a3238a.sqlite3"
BACKUP_SHA256 = "7f8349a3238afa1cf32fd124fbdf757b0617bb09ada82a1ed4159a4e9f6fb0e3"
SUPP = (ROOT / "data" / "external" / "ecdsafail" / "research_supplement" / "jieyilong_ecdsafail-supplemental-materials" / "extracted"
        / "ecdsafail-supplemental-materials-ef7ecd2b27cf27881874468e003e53e16ba48075" / "frontier_reconstruction" / "data" / "raw")
BODIES = {
    "a4dbc0f22aa15ca0cfd49f41eb5fddabf8b028dd6df76cbc57eca350807104af": ROOT / "data" / "external" / "ecdsafail" / "evaluation" / "ecdsafail_eval_submissions_20260924T072124Z_a4dbc0f22aa1.json",
    "915af8b7be4283dbd357d5e8ed013d91c494e81fcee6528aa3dc1385706aebec": SUPP / "official_submissions_2026-07-27T161558Z.json",
}
EXAMPLE_UUID = "3ccbe24c-0429-4ea9-972a-80afb1421679"
EXAMPLE_SUBMISSION_ID = "ebb6a943f55c4497a7d0b39678813ff8"
NEW_TABLES = ["submission_detail_source", "submission_source_detail"]
ABSENT = object()


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def ro(p: Path):
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def table_digest(con, table):
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    h, n = hashlib.sha256(), 0
    for row in con.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY {', '.join(cols)}"):
        h.update(json.dumps(list(row), default=str).encode())
        n += 1
    return n, h.hexdigest()


def presence(v):
    return "absent" if v is ABSENT else "null" if v is None else "empty" if v in ("", []) else "value"


def main() -> None:
    db_before = sha(DB)
    out: dict = {"db_sha256": db_before, "db_bytes": DB.stat().st_size,
                 "backup": {"path": BACKUP.relative_to(ROOT).as_posix(), "sha256": sha(BACKUP), "bytes": BACKUP.stat().st_size}}
    out["backup"]["matches_recorded_sha256"] = out["backup"]["sha256"] == BACKUP_SHA256
    prod, back = ro(DB), ro(BACKUP)
    out["integrity_check"] = prod.execute("PRAGMA integrity_check").fetchone()[0]
    out["foreign_key_violations"] = len(prod.execute("PRAGMA foreign_key_check").fetchall())
    out["journal_mode"] = prod.execute("PRAGMA journal_mode").fetchone()[0]
    out["migrations"] = [r[0] for r in prod.execute("SELECT version FROM schema_migrations ORDER BY version")]

    # pre-existing content and schema vs the backup
    tables = [r[0] for r in back.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    unchanged = {}
    for t in tables:
        a, b = table_digest(back, t), table_digest(prod, t)
        unchanged[t] = {"rows_before": a[0], "rows_after": b[0], "identical": a == b}
    out["existing_tables"] = unchanged
    out["existing_tables_changed"] = [t for t, v in unchanged.items() if not v["identical"]]
    sm_before = [tuple(r) for r in back.execute("SELECT version, applied_at FROM schema_migrations ORDER BY version")]
    sm_after = [tuple(r) for r in prod.execute("SELECT version, applied_at FROM schema_migrations ORDER BY version")]
    out["schema_migrations_only_row_8_added"] = sm_after[:-1] == sm_before and sm_after[-1][0] == 8
    ddl_before = {(r[0], r[1]): r[2] for r in back.execute("SELECT type, name, sql FROM sqlite_master")}
    ddl_after = {(r[0], r[1]): r[2] for r in prod.execute("SELECT type, name, sql FROM sqlite_master")}
    out["existing_schema_objects_unchanged"] = all(ddl_after.get(k) == v for k, v in ddl_before.items())
    out["new_schema_objects"] = sorted(f"{k[0]} {k[1]}" for k in ddl_after.keys() - ddl_before.keys())
    out["protected_facts"] = {
        "technique_labels": prod.execute("SELECT count(*) FROM version_technique_label").fetchone()[0],
        "populations": [tuple(r) for r in prod.execute("SELECT p.name, p.population_role, count(m.submission_id) FROM research_population p LEFT JOIN population_membership m USING (population_id) GROUP BY p.population_id ORDER BY p.name")],
        "submitter_contributions": prod.execute("SELECT count(*) FROM contribution WHERE role = 'SUBMITTER'").fetchone()[0],
        "declared_coauthor_contributions": prod.execute("SELECT count(*) FROM contribution WHERE role = 'COAUTHOR'").fetchone()[0],
        "contributor_identities": prod.execute("SELECT count(*) FROM contributor_identity").fetchone()[0],
        "evaluations_with_official_score": prod.execute("SELECT count(*) FROM submission_evaluation WHERE official_score IS NOT NULL").fetchone()[0],
    }

    # new rows vs the raw bodies
    out["new_table_counts"] = {t: prod.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in NEW_TABLES}
    qch = {r[0]: r[1] for r in prod.execute("SELECT external_submission_key, submission_id FROM submission WHERE source_system = 'ecdsafail'")}
    sources = []
    email_re = re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}")
    for src in prod.execute("SELECT * FROM submission_detail_source ORDER BY fetched_at"):
        body_path = BODIES[src["detail_source_id"]]
        body = body_path.read_bytes()
        records = json.loads(body)["submissions"]
        by_id = {r["id"]: (i, r) for i, r in enumerate(records)}
        stored = {r["source_submission_uuid"]: r for r in prod.execute("SELECT * FROM submission_source_detail WHERE detail_source_id = ?", (src["detail_source_id"],))}
        mismatches = collections.Counter()
        cover = {f: collections.Counter() for f in ("note", "claimedScore", "coauthors")}
        for uuid, (index, rec) in by_id.items():
            row = stored.get(uuid)
            if row is None:
                mismatches["missing_row"] += 1
                continue
            note, claimed, co = rec.get("note", ABSENT), rec.get("claimedScore", ABSENT), rec.get("coauthors", ABSENT)
            for f, v in (("note", note), ("claimedScore", claimed), ("coauthors", co)):
                cover[f][presence(v)] += 1
            checks = {
                "note_text": row["note"] == (note if isinstance(note, str) else None),
                "note_sha256": row["note_sha256"] == (hashlib.sha256(note.encode("utf-8")).hexdigest() if isinstance(note, str) else None),
                "note_chars": row["note_chars"] == (len(note) if isinstance(note, str) else None),
                "note_presence": row["note_presence"] == presence(note),
                "claimed_presence": row["claimed_score_presence"] == presence(claimed),
                "claimed_value": (row["claimed_score_integer"] if row["claimed_score_json"] is not None else None) == (claimed if isinstance(claimed, int) else None)
                                 and row["claimed_score_json"] == (str(claimed) if isinstance(claimed, int) else None),
                "coauthors_presence": row["coauthors_presence"] == presence(co),
                "coauthors_structure": (json.loads(row["coauthors_json"]) if row["coauthors_json"] is not None else None) == (co if isinstance(co, list) else None),
                "created_at": row["platform_created_at_raw"] == rec.get("createdAt"),
                "updated_at": row["platform_updated_at_raw"] == rec.get("updatedAt"),
                "record_index": row["source_record_index"] == index,
                "fetched_at": row["source_fetched_at"] == src["fetched_at"],
                "link_is_exact_uuid_match": row["submission_id"] == qch.get("ecdsafail:" + uuid),
            }
            for name, ok in checks.items():
                if not ok:
                    mismatches[name] += 1
        notes = [r["note"] for r in stored.values() if r["note"] is not None]
        sources.append({
            "detail_source_id": src["detail_source_id"], "fetched_at": src["fetched_at"], "acquisition": src["acquisition"],
            "evaluation_snapshot_id": src["evaluation_snapshot_id"], "body_path": body_path.relative_to(ROOT).as_posix(),
            "body_sha256_ok": hashlib.sha256(body).hexdigest() == src["body_sha256"] == src["detail_source_id"], "body_bytes": len(body),
            "source_records": len(records), "unique_uuids": len(by_id), "stored_rows": len(stored), "extra_stored_rows": len(stored.keys() - by_id.keys()),
            "matched_to_qch_submission": sum(1 for r in stored.values() if r["submission_id"] is not None),
            "unmatched_stored_with_null_link": sum(1 for r in stored.values() if r["submission_id"] is None),
            "field_coverage_from_raw_body": {f: dict(c) for f, c in cover.items()},
            "field_mismatches_vs_raw_body": dict(mismatches),
            "stored_note_chars": sum(map(len, notes)), "stored_note_utf8_bytes": sum(len(n.encode("utf-8")) for n in notes),
            "notes_containing_an_email_like_string": sum(bool(email_re.search(n)) for n in notes),
        })
    out["sources"] = sources
    out["all_rows_equal_their_source"] = all(not s["field_mismatches_vs_raw_body"] and s["extra_stored_rows"] == 0 and s["body_sha256_ok"] for s in sources)

    # latest rule
    latest = [dict(r) for r in prod.execute("SELECT source_submission_uuid, count(*) n FROM submission_source_detail_latest GROUP BY 1 HAVING n > 1")]
    wrong = prod.execute(
        "SELECT count(*) FROM submission_source_detail_latest l WHERE l.source_fetched_at <> (SELECT max(d.source_fetched_at) FROM submission_source_detail d"
        " WHERE d.source_system = l.source_system AND d.source_submission_uuid = l.source_submission_uuid)").fetchone()[0]
    out["latest_view"] = {"rows": prod.execute("SELECT count(*) FROM submission_source_detail_latest").fetchone()[0],
                          "distinct_platform_submissions": prod.execute("SELECT count(DISTINCT source_submission_uuid) FROM submission_source_detail").fetchone()[0],
                          "uuids_with_more_than_one_latest_row": len(latest), "rows_not_from_the_most_recent_body": wrong,
                          "linked_to_qch_submission": prod.execute("SELECT count(*) FROM submission_source_detail_latest WHERE submission_id IS NOT NULL").fetchone()[0]}
    out["qch_submissions_without_any_detail"] = [r[0] for r in prod.execute(
        "SELECT s.external_submission_key FROM submission s WHERE NOT EXISTS (SELECT 1 FROM submission_source_detail d WHERE d.submission_id = s.submission_id) ORDER BY 1")]

    # required example
    src_rec = next(r for r in json.loads(BODIES["a4dbc0f22aa15ca0cfd49f41eb5fddabf8b028dd6df76cbc57eca350807104af"].read_bytes())["submissions"] if r["id"] == EXAMPLE_UUID)
    ex = prod.execute(
        "SELECT l.*, v.external_version_key, e.official_score, e.platform_created_at, e.platform_updated_at, i.current_handle,"
        " (SELECT count(*) FROM version_technique_label t WHERE t.version_id = v.version_id) AS labels,"
        " (SELECT count(*) FROM submission_source_detail d WHERE d.submission_id = l.submission_id) AS history_rows"
        " FROM submission_source_detail_latest l JOIN submission s ON s.submission_id = l.submission_id"
        " LEFT JOIN circuit_version v ON v.realized_from_submission_id = s.submission_id"
        " LEFT JOIN submission_evaluation e ON e.submission_id = s.submission_id"
        " LEFT JOIN contribution c ON c.submission_id = s.submission_id AND c.role = 'SUBMITTER'"
        " LEFT JOIN contributor_identity i ON i.contributor_identity_id = c.contributor_identity_id"
        " WHERE l.source_submission_uuid = ?", (EXAMPLE_UUID,)).fetchone()
    out["example"] = {
        "source_submission_uuid": EXAMPLE_UUID, "submission_id": ex["submission_id"], "submission_id_as_expected": ex["submission_id"] == EXAMPLE_SUBMISSION_ID,
        "version": ex["external_version_key"], "submitter_current_handle": ex["current_handle"],
        "platform_created_at_raw": ex["platform_created_at_raw"], "platform_updated_at_raw": ex["platform_updated_at_raw"],
        "timestamps_equal_submission_evaluation": (ex["platform_created_at_raw"], ex["platform_updated_at_raw"]) == (ex["platform_created_at"], ex["platform_updated_at"]),
        "claimed_score_presence": ex["claimed_score_presence"], "claimed_score_json": ex["claimed_score_json"], "official_score": ex["official_score"],
        "coauthors_presence": ex["coauthors_presence"], "coauthors_json": ex["coauthors_json"],
        "note_chars": ex["note_chars"], "source_note_chars": len(src_rec["note"]),
        "note_equal_character_for_character": ex["note"] == src_rec["note"],
        "note_sha256": ex["note_sha256"], "source_note_sha256": hashlib.sha256(src_rec["note"].encode("utf-8")).hexdigest(),
        "author_assigned_technique_labels_for_this_version": ex["labels"], "detail_history_rows": ex["history_rows"],
        "from_detail_source": ex["detail_source_id"],
    }
    e = out["example"]
    prod.close()
    back.close()
    out["db_sha256_unchanged_during_validation"] = sha(DB) == db_before
    ok = (out["backup"]["matches_recorded_sha256"] and out["integrity_check"] == "ok" and out["foreign_key_violations"] == 0
          and out["migrations"] == [1, 2, 3, 4, 5, 6, 7, 8] and out["existing_tables_changed"] == ["schema_migrations"]
          and out["schema_migrations_only_row_8_added"] and out["existing_schema_objects_unchanged"] and out["all_rows_equal_their_source"]
          and out["latest_view"]["uuids_with_more_than_one_latest_row"] == 0 and out["latest_view"]["rows_not_from_the_most_recent_body"] == 0
          and out["latest_view"]["rows"] == out["latest_view"]["distinct_platform_submissions"]
          and e["submission_id_as_expected"] and e["version"] == "ecdsafail:9700396" and e["submitter_current_handle"] == "jackylee0424"
          and e["note_equal_character_for_character"] and e["note_sha256"] == e["source_note_sha256"] and e["claimed_score_presence"] == "null"
          and e["coauthors_json"] == "[]" and e["author_assigned_technique_labels_for_this_version"] == 0 and e["timestamps_equal_submission_evaluation"]
          and out["protected_facts"]["technique_labels"] == 404 and out["db_sha256_unchanged_during_validation"])
    out["all_checks_pass"] = ok
    print(json.dumps(out, indent=1, default=str, ensure_ascii=False))
    if "--json" in sys.argv:
        p = Path(sys.argv[sys.argv.index("--json") + 1])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
