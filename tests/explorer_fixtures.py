"""A small synthetic QCH store for the Version Evolution Explorer tests.

Built through the public hub API where one exists, with direct inserts (into
the TEMPORARY file only) for evaluation / contributor / technique-label /
artifact rows. Nothing here touches the production store.

Shape (names -> version keys):
  a  demo:aaaa111  accepted Q100 T1000 S100000, submitter alice, label 'V1'
  b  demo:bbbb222  accepted Q110 T800  S88000   (a->b: Q+10, T-200, S-12000, reduction 12%), two source snapshots,
                   two author labels from two sources on its incoming edge a->b (next_promoted_commit)
  c  demo:cccc333  rejected, stored rejection reason
  d  demo:dddd444  no submission at all; carries a self-link
  zero demo:zero000 official metrics all 0 (zero denominator), edge zero->b
  nomet demo:nomet00 evaluation 'failed': no official metrics, edge nomet->b
  e,f demo:abc1234 / demo:abc1299   ambiguous fragment 'abc12'
  hub demo:hub0000 with 30 leaves demo:leaf000..leaf029 (node-limit tests)
  o  other:xyz9999  in a second logical circuit; o and a both carry the label 'V1'
Edges: a->b next_promoted_commit and a->b branched_from (parallel pair); b->c next_promoted_commit;
c->a historical_successor (a cycle a->b->c->a); d->d branched_from (self-link); zero->b optimizes
(a relation type nothing knows); nomet->b next_promoted_commit; hub->leaf branched_from.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from qch import QCH
from qch.models import SubmissionDetailSource, SubmissionSourceDetail

SYSTEM = "demo"
T_FETCH_OLD = "2026-07-27T16:16:01Z"
T_FETCH_NEW = "2026-09-24T07:21:24Z"
NOTE_NEW = "Edited note\r\nsecond line with ünïcødé ✓ and <b>tags</b> & **markdown**\n\nlast line"
NOTE_OLD = "First draft note"
LONG_NOTE = ("line of text\n" * 400)  # 5,200 characters: longer than the preview limit
TRICKY_NOTE = "\n  indented start\n\ttabbed line   \n\n\n"  # leading newline, indentation, tab, trailing spaces and blank lines
UUID_B = "00000000-0000-4000-8000-000000000002"  # the platform UUID of submission b (see _uuid(2))


def _uuid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def build_explorer_db(path: Path, *, with_optional: bool = True) -> dict[str, str]:
    """Creates the fixture store at `path` and returns name -> id (versions, edges, submissions, circuits)."""
    ids: dict[str, str] = {}
    with QCH.open(str(path)) as hub:
        c1 = hub.circuits.create("qch:logical:demo_add", "demo_add")
        c2 = hub.circuits.create("qch:logical:demo_other", "demo_other")
        ids["circuit1"], ids["circuit2"] = c1.logical_circuit_id, c2.logical_circuit_id

        def submission(name: str, n: int) -> str:
            sub = hub.submissions.create(SYSTEM, external_submission_key=f"{SYSTEM}:{_uuid(n)}")
            ids[f"sub_{name}"] = sub.submission_id
            return sub.submission_id

        def version(name: str, key: str, *, circuit=c1, time: str | None = None, sub: str | None = None, label: str | None = None) -> str:
            v = hub.versions.create(circuit.logical_circuit_id, external_version_key=key, version_label=label, historical_time=time, realized_from_submission_id=sub)
            ids[name] = v.version_id
            return v.version_id

        sa, sb, sc, sz, sn = submission("a", 1), submission("b", 2), submission("c", 3), submission("zero", 4), submission("nomet", 5)
        version("a", "demo:aaaa111", time="2026-01-01T00:00:00Z", sub=sa, label="V1")
        version("b", "demo:bbbb222", time="2026-01-02T00:00:00Z", sub=sb)
        version("c", "demo:cccc333", time="2026-01-03T00:00:00Z", sub=sc)
        version("d", "demo:dddd444", time="2026-01-04T00:00:00Z")
        version("zero", "demo:zero000", time="2025-12-01T00:00:00Z", sub=sz)
        version("nomet", "demo:nomet00", time="2025-12-02T00:00:00Z", sub=sn)
        version("e", "demo:abc1234", time="2026-02-01T00:00:00Z")
        version("f", "demo:abc1299", time="2026-02-02T00:00:00Z")
        version("o", "other:xyz9999", circuit=c2, label="V1")
        version("hub", "demo:hub0000", time="2026-03-01T00:00:00Z")
        for i in range(30):
            version(f"leaf{i:03d}", f"demo:leaf{i:03d}", time=f"2026-03-{(i % 28) + 2:02d}T{i % 24:02d}:00:00Z")

        def edge(name: str, source: str, target: str, relation: str, **kwargs) -> None:
            ids[name] = hub.evolution.add_edge(ids[source], ids[target], relation, **kwargs).edge_id

        edge("e_ab_next", "a", "b", "next_promoted_commit")
        edge("e_ab_branch", "a", "b", "branched_from", description="stored description", evidence={"note": "kept verbatim", "n": 3})
        edge("e_bc", "b", "c", "next_promoted_commit")
        edge("e_ca", "c", "a", "historical_successor")
        edge("e_dd", "d", "d", "branched_from")
        edge("e_zb", "zero", "b", "optimizes")
        edge("e_nb", "nomet", "b", "next_promoted_commit")
        for i in range(30):
            edge(f"e_hub{i:03d}", "hub", f"leaf{i:03d}", "branched_from")

    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        _insert_evaluations(conn, ids)
        if with_optional:
            _insert_contributors(conn, ids)
            _insert_labels(conn, ids)
            _insert_artifact(conn, ids)
        conn.commit()
    finally:
        conn.close()
    with QCH.open(str(path)) as hub:
        _record_details(hub, ids)
    return ids


def _insert_evaluations(conn: sqlite3.Connection, ids: dict[str, str]) -> None:
    conn.execute(
        "INSERT INTO evaluation_snapshot(snapshot_id, source_system, source_endpoint, benchmark_id, benchmark_source_ref, fetched_at, http_etag, body_sha256,"
        " body_bytes, record_count, importer_name, importer_version, imported_at, import_stats_json) VALUES ('snap1', ?, 'https://example.test/api', 'bench', 'ref', ?, NULL,"
        " 'snap1', 1, 5, 'test', '1', ?, '{}')", (SYSTEM, T_FETCH_NEW, T_FETCH_NEW))
    rows = [
        ("a", "accepted", "promoted", 100, 1000, 100000, None),
        ("b", "accepted", "promoted", 110, 800, 88000, None),
        ("c", "rejected", None, 90, 2000, 180000, "score did not improve current best"),
        ("zero", "accepted", "promoted", 0, 0, 0, None),
        ("nomet", "failed", None, None, None, None, "evaluator crashed"),
    ]
    for name, status, promo, q, t, s, reason in rows:
        conn.execute(
            "INSERT INTO submission_evaluation(evaluation_id, snapshot_id, submission_id, source_submission_uuid, platform_status, rejection_reason, promotion_status,"
            " promotion_reason, official_peak_qubits, official_avg_executed_toffoli, official_score, submission_commit_sha, promoted_source_ref, platform_created_at,"
            " platform_updated_at, promotion_finished_at, source_record_sha256) VALUES (?, 'snap1', ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'x')",
            (f"ev_{name}", ids[f"sub_{name}"], "uuid", status, reason, promo, q, t, s, f"{name}" * 10, f"{name}" * 10 if promo else None,
             "2026-01-05T10:00:00.123Z", "2026-01-05T10:02:00.456Z", "2026-01-05T10:02:00.456Z" if promo else None))


def _insert_contributors(conn: sqlite3.Connection, ids: dict[str, str]) -> None:
    conn.execute("INSERT INTO contributor_identity VALUES ('id_alice', ?, 'platform_account', 'acct-1', 'alice', 'snap1', 'snap1', ?)", (SYSTEM, T_FETCH_NEW))
    conn.execute("INSERT INTO contributor_alias VALUES ('al1', 'id_alice', 'handle', 'alice', 'alice', 1, 'AUTHORITATIVE', 'demo_api:solverUsername', 'snap1', '{}', ?)", (T_FETCH_NEW,))
    conn.execute("INSERT INTO contributor_alias VALUES ('al2', 'id_alice', 'handle', 'old_alice', 'old_alice', 0, 'DERIVED', 'git trailer', 'snap1', '{}', ?)", (T_FETCH_NEW,))
    for name in ("a", "b", "c"):
        conn.execute(
            "INSERT INTO contribution VALUES (?, 'snap1', ?, 'uuid', 'SUBMITTER', 'AUTHORITATIVE', 'id_alice', NULL, 'solverAccountId', ?)",
            (f"ct_{name}", ids[f"sub_{name}"], T_FETCH_NEW))
    conn.execute("INSERT INTO contribution VALUES ('ct_b_co', 'snap1', ?, 'uuid', 'COAUTHOR', 'DECLARED', NULL, 'bob the free-text coauthor', 'coauthors', ?)", (ids["sub_b"], T_FETCH_NEW))


def _insert_labels(conn: sqlite3.Connection, ids: dict[str, str]) -> None:
    for source_id, file_path in (("rs1", "appendix.tex"), ("rs2", "second_census.tex")):
        conn.execute(
            "INSERT INTO research_source(source_id, source_kind, repository_url, repository_commit, file_path, sha256, auxiliary_json, license_notice, description,"
            " retrieved_at, importer_name, importer_version, imported_at) VALUES (?, 'technique_census', 'https://example.test/paper', ?, ?, ?, '{}', 'n/a', 'd', 't', 'i', '1', 't')",
            (source_id, "c" * 40, file_path, "d" * 64))
    for label_id, source_id, version, edge_name, family, change, row in (
        ("lb1", "rs1", "b", "e_ab_next", "Windowing", "K2 pair compressor", 1),
        ("lb2", "rs2", "b", "e_ab_next", "Fusion", None, 2),  # a second label for the SAME version and edge (other source): must not duplicate the graph edge
    ):
        conn.execute(
            "INSERT INTO version_technique_label(label_id, source_id, version_id, incoming_edge_id, source_row_number, source_commit_ref, resolved_commit_sha, label_role, family,"
            " change_type, classification_status, source_contestant, source_date, source_scored, source_score, source_frontier_delta) VALUES (?, ?, ?, ?, ?, 'bbbb222', NULL,"
            " 'primary', ?, ?, 'author_final_primary_label', 'carol', '2026-01-02', 1, 1, 1)", (label_id, source_id, ids[version], ids[edge_name], row, family, change))


def _insert_artifact(conn: sqlite3.Connection, ids: dict[str, str]) -> None:
    conn.execute(
        "INSERT INTO artifact(artifact_id, version_id, artifact_type, format, uri, sha256, size_bytes, status, created_at, metadata_json) VALUES"
        " ('art1', ?, 'circuit', 'kmx', '../somewhere/missing.kmx', ?, 12345, 'READY', '2026-01-05T00:00:00.000000+00:00', '{}')", (ids["b"], "e" * 64))


def _record_details(hub: QCH, ids: dict[str, str]) -> None:
    def source(sha: str, fetched: str, acquisition: str) -> SubmissionDetailSource:
        return SubmissionDetailSource(detail_source_id=sha, source_system=SYSTEM, source_endpoint="https://example.test/api", fetched_at=fetched, acquisition=acquisition,
                                      acquisition_note="fixture", body_sha256=sha, body_bytes=1, record_count=3, importer_name="t", importer_version="1", imported_at="t")

    def detail(sha: str, fetched: str, name: str, uuid: str, **fields) -> SubmissionSourceDetail:
        base = dict(detail_id=f"{sha}:{uuid}", detail_source_id=sha, source_system=SYSTEM, source_submission_uuid=uuid, source_record_index=0, source_fetched_at=fetched,
                    note_presence="absent", claimed_score_presence="absent", coauthors_presence="absent", importer_version="1", imported_at="t",
                    submission_id=ids.get(f"sub_{name}"))
        base.update(fields)
        return SubmissionSourceDetail(**base)

    old, new = "a" * 64, "b" * 64
    hub.submission_details.record(source(old, T_FETCH_OLD, "third_party_pinned_copy"), [
        detail(old, T_FETCH_OLD, "b", UUID_B, note_presence="value", note=NOTE_OLD, note_sha256="0" * 64, note_chars=len(NOTE_OLD),
               claimed_score_presence="value", claimed_score_json="90000", claimed_score_integer=90000,
               platform_created_at_raw="2026-01-05T10:00:00.123Z", platform_updated_at_raw="2026-01-05T10:02:00.456Z"),
    ])
    hub.submission_details.record(source(new, T_FETCH_NEW, "qch_fetch"), [
        detail(new, T_FETCH_NEW, "b", UUID_B, note_presence="value", note=NOTE_NEW, note_sha256="1" * 64, note_chars=len(NOTE_NEW),
               claimed_score_presence="value", claimed_score_json="88000", claimed_score_integer=88000, coauthors_presence="value", coauthors_json='["bob the free-text coauthor"]',
               platform_created_at_raw="2026-01-05T10:00:00.123Z", platform_updated_at_raw="2026-01-05T10:02:00.456Z"),
        detail(new, T_FETCH_NEW, "a", _uuid(1), note_presence="null", claimed_score_presence="null", coauthors_presence="empty", coauthors_json="[]"),
        detail(new, T_FETCH_NEW, "c", _uuid(3), note_presence="value", note=LONG_NOTE, note_sha256="2" * 64, note_chars=len(LONG_NOTE), claimed_score_presence="absent"),
        detail(new, T_FETCH_NEW, "nomet", _uuid(5), note_presence="value", note=TRICKY_NOTE, note_sha256="3" * 64, note_chars=len(TRICKY_NOTE)),
    ])
    hub.submission_details.record(source("c" * 64, "2026-10-01T00:00:00Z", "qch_fetch"), [])  # a third, newer, EMPTY snapshot: must not hide older details


def drop_optional_tables(path: Path, tables: list[str]) -> None:
    """Removes optional tables/views from a fixture copy (simulating an older or smaller QCH database)."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        for name in tables:
            kind = conn.execute("SELECT type FROM sqlite_master WHERE name = ?", (name,)).fetchone()
            if kind:
                conn.execute(f"DROP {kind[0].upper()} {name}")
        conn.commit()
    finally:
        conn.close()
