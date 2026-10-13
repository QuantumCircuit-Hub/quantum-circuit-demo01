"""QCH schema v8: ECDSA.Fail submitter-authored submission details (note,
claimedScore, coauthors declarations) per source body.

Synthetic tests build their own tiny store and API bodies. Real-data tests
run on a TEMPORARY COPY of the real store -- never on
data/qch_ecdsa_p17.sqlite3 itself -- and skip when the git-ignored source
snapshots are absent.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qch import QCH  # noqa: E402
from qch.exceptions import ValidationError  # noqa: E402
from qch.importers.ecdsafail_evaluation import SUBMISSIONS_URL, ImportBlockedError, SnapshotIntegrityError  # noqa: E402
from qch.importers.ecdsafail_submission_details import (  # noqa: E402
    apply_submission_details,
    cross_snapshot_report,
    detail_id,
    load_pinned_supplement_snapshot,
    load_qch_snapshot,
    plan_submission_details,
)
from qch.models import SubmissionSourceDetail  # noqa: E402

REAL_DB = PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
REAL_SNAPSHOT = PROJECT_ROOT / "data" / "external" / "ecdsafail" / "evaluation" / "ecdsafail_eval_submissions_20260924T072124Z_a4dbc0f22aa1.json"
REAL_SNAPSHOT_SHA256 = "a4dbc0f22aa15ca0cfd49f41eb5fddabf8b028dd6df76cbc57eca350807104af"
MANIFEST = PROJECT_ROOT / "data" / "external" / "ecdsafail" / "research_supplement" / "SOURCE_MANIFEST.json"
needs_real = pytest.mark.skipif(not (REAL_DB.exists() and REAL_SNAPSHOT.exists() and MANIFEST.exists()), reason="real store or source snapshots not present")

U1, U2, U3, U4, U5 = (f"{i:08d}-0000-4000-8000-00000000000{i}" for i in range(1, 6))
LONG_NOTE = "# Title — ünïcødé ✓ 量子\r\nline two\n\n| a | b |\n" + ("x" * 30000) + "\n  trailing spaces  \n"
DETAIL_TABLES = ["submission_detail_source", "submission_source_detail"]


def _record(uuid: str, **fields) -> dict:
    return {"id": uuid, "createdAt": "2026-09-01T00:00:00.000Z", "updatedAt": "2026-09-01T00:05:00.123Z", **fields}


def _snapshot(tmp_path: Path, name: str, body: bytes | list, fetched_at: str = "2026-09-24T07:21:24Z"):
    """Writes a body + .meta.json sidecar the way QCH's own fetcher does and loads it."""
    if isinstance(body, list):
        body = json.dumps({"submissions": body}, ensure_ascii=False).encode("utf-8")
    path = tmp_path / f"{name}.json"
    path.write_bytes(body)
    (tmp_path / f"{name}.meta.json").write_text(json.dumps(
        {"source_endpoint": SUBMISSIONS_URL, "fetched_at": fetched_at, "body_sha256": hashlib.sha256(body).hexdigest()}), encoding="utf-8")
    return load_qch_snapshot(path)


@pytest.fixture
def hub(tmp_path):
    with QCH.open(str(tmp_path / "store.sqlite3")) as h:
        for uuid in (U1, U2, U3):
            h.submissions.create("ecdsafail", external_submission_key=f"ecdsafail:{uuid}")
        yield h


def _counts(h, tables):
    conn = h._storage._conn
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}


def _rows(h):
    conn = h._storage._conn
    return [tuple(r) for t in DETAIL_TABLES for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")]


def _by_uuid(h, uuid):
    return {d.detail_source_id: d for d in h.submission_details.by_source_uuid(uuid)}


# == fidelity ====================================================================================


def test_long_note_with_newlines_and_unicode_is_stored_character_for_character(hub, tmp_path):
    snap = _snapshot(tmp_path, "a", [_record(U1, note=LONG_NOTE, claimedScore=None, coauthors=[])])
    apply_submission_details(hub, [plan_submission_details(hub, snap)])
    stored = hub.submission_details.latest(hub.submissions.find_by_external_key(f"ecdsafail:{U1}").submission_id)
    assert stored.note == LONG_NOTE and stored.note_chars == len(LONG_NOTE)  # \r\n, em dash, CJK, trailing spaces: all intact
    assert stored.note_sha256 == hashlib.sha256(LONG_NOTE.encode("utf-8")).hexdigest()
    raw = hub._storage._conn.execute("SELECT note FROM submission_source_detail WHERE detail_id = ?", (stored.detail_id,)).fetchone()[0]
    assert raw == LONG_NOTE


def test_absent_null_and_empty_are_distinguished(hub, tmp_path):
    snap = _snapshot(tmp_path, "a", [
        _record(U1),  # every optional field absent
        _record(U2, note=None, claimedScore=None, coauthors=None),
        {"id": U3, "note": "", "claimedScore": 0, "coauthors": []},  # empty string / zero / empty array; timestamps absent
    ])
    apply_submission_details(hub, [plan_submission_details(hub, snap)])
    a, b, c = (next(iter(_by_uuid(hub, u).values())) for u in (U1, U2, U3))
    assert (a.note_presence, a.claimed_score_presence, a.coauthors_presence) == ("absent", "absent", "absent")
    assert (a.note, a.claimed_score_json, a.coauthors_json) == (None, None, None)
    assert (b.note_presence, b.claimed_score_presence, b.coauthors_presence) == ("null", "null", "null")
    assert (b.note, b.claimed_score_json, b.coauthors_json) == (None, None, None)
    assert (c.note_presence, c.claimed_score_presence, c.coauthors_presence) == ("empty", "value", "empty")
    assert (c.note, c.claimed_score_json, c.claimed_score_integer, c.coauthors_json) == ("", "0", 0, "[]")  # zero is a value, not null
    assert (c.platform_created_at_raw, c.platform_updated_at_raw) == (None, None)
    assert (a.platform_created_at_raw, a.platform_updated_at_raw) == ("2026-09-01T00:00:00.000Z", "2026-09-01T00:05:00.123Z")  # original strings


def test_claimed_score_keeps_the_source_token_type_and_precision(hub, tmp_path):
    body = ('{"submissions": [{"id": "%s", "claimedScore": 1137367864}, {"id": "%s", "claimedScore": 1.50},'
            ' {"id": "%s", "claimedScore": 123456789012345678901234567890}]}' % (U1, U2, U3)).encode()
    apply_submission_details(hub, [plan_submission_details(hub, _snapshot(tmp_path, "a", body))])
    a, b, c = (next(iter(_by_uuid(hub, u).values())) for u in (U1, U2, U3))
    assert (a.claimed_score_json, a.claimed_score_integer) == ("1137367864", 1137367864)
    assert (b.claimed_score_json, b.claimed_score_integer) == ("1.50", None)  # a float stays a float, trailing zero kept
    assert (c.claimed_score_json, c.claimed_score_integer) == ("123456789012345678901234567890", None)  # beyond int64: token only, no rounding


def test_coauthor_declarations_keep_their_structure_and_are_not_resolved(hub, tmp_path):
    before = _counts(hub, ["contribution", "contributor_identity", "contributor_alias"])
    snap = _snapshot(tmp_path, "a", [_record(U1, coauthors=["Åsa", "bob"]), _record(U2, coauthors=[{"name": "x", "n": 1}, ["nested"]])])
    apply_submission_details(hub, [plan_submission_details(hub, snap)])
    a, b = (next(iter(_by_uuid(hub, u).values())) for u in (U1, U2))
    assert json.loads(a.coauthors_json) == ["Åsa", "bob"] and "Åsa" in a.coauthors_json
    assert json.loads(b.coauthors_json) == [{"name": "x", "n": 1}, ["nested"]]
    assert _counts(hub, ["contribution", "contributor_identity", "contributor_alias"]) == before  # nothing bound to an identity


# == history, matching, conflicts ================================================================


def test_multiple_snapshots_keep_history_and_latest_ignores_insertion_order(hub, tmp_path):
    old = plan_submission_details(hub, _snapshot(tmp_path, "old", [_record(U1, note="first draft")], fetched_at="2026-07-27T16:16:01Z"))
    new = plan_submission_details(hub, _snapshot(tmp_path, "new", [_record(U1, note="edited", coauthors=[]), _record(U2, note="only new")], fetched_at="2026-09-24T07:21:24Z"))
    hub.submission_details.record(new.source, new.details)  # NEWER body inserted FIRST
    hub.submission_details.record(old.source, old.details)
    sid = hub.submissions.find_by_external_key(f"ecdsafail:{U1}").submission_id
    assert [d.note for d in hub.submission_details.history(sid)] == ["first draft", "edited"]  # both kept, oldest fetch first
    latest = hub.submission_details.latest(sid)
    assert (latest.note, latest.coauthors_presence, latest.detail_source_id) == ("edited", "empty", new.source.detail_source_id)
    view = hub._storage._conn.execute("SELECT source_submission_uuid, count(*) FROM submission_source_detail_latest GROUP BY 1").fetchall()
    assert sorted(tuple(r) for r in view) == [(U1, 1), (U2, 1)]  # exactly one latest row per platform submission
    report = cross_snapshot_report([new, old])
    assert report["pairs"][0]["note_differs"] == 1 and report["pairs"][0]["only_in_newer"] == 1


def test_matching_is_by_exact_uuid_and_unmatched_records_get_a_null_link(hub, tmp_path):
    before = _counts(hub, ["submission", "circuit_version", "submission_evaluation", "transformation_edge"])
    plan = plan_submission_details(hub, _snapshot(tmp_path, "a", [_record(U1, note="a"), _record(U4, note="no QCH submission")]))
    assert (plan.stats["matched_to_qch_submission"], plan.stats["unmatched"]) == (1, 1)
    assert [u["source_submission_uuid"] for u in plan.unmatched] == [U4]
    apply_submission_details(hub, [plan])
    unlinked = next(iter(_by_uuid(hub, U4).values()))
    assert unlinked.submission_id is None and unlinked.note == "no QCH submission"
    assert _counts(hub, ["submission", "circuit_version", "submission_evaluation", "transformation_edge"]) == before  # nothing created


@pytest.mark.parametrize("records, needle", [
    ([_record(U1, note="a"), _record(U1, note="b")], "already appeared"),  # conflicting duplicate UUID
    ([_record(U1, note=123)], "note is int"),
    ([_record(U1, claimedScore="12")], "claimedScore is str"),
    ([_record(U1, claimedScore=True)], "claimedScore is bool"),
    ([_record(U1, coauthors="bob")], "coauthors is str"),
    ([{"id": U1, "createdAt": "2026-09-01 00:00:00"}], "createdAt"),  # no explicit timezone
    ([{"id": "not-a-uuid"}], "UUID"),
])
def test_conflicting_or_invalid_records_block_everything(hub, tmp_path, records, needle):
    good = plan_submission_details(hub, _snapshot(tmp_path, "good", [_record(U2, note="fine")], fetched_at="2026-07-27T16:16:01Z"))
    bad = plan_submission_details(hub, _snapshot(tmp_path, "bad", records))
    assert any(needle in issue for issue in bad.blocking)
    with pytest.raises(ImportBlockedError):
        apply_submission_details(hub, [good, bad])
    assert _counts(hub, DETAIL_TABLES) == {t: 0 for t in DETAIL_TABLES}  # the valid body was not written either


def test_identical_duplicate_record_is_reported_and_kept_once(hub, tmp_path):
    plan = plan_submission_details(hub, _snapshot(tmp_path, "a", [_record(U1, note="a"), _record(U1, note="a")]))
    assert plan.blocking == [] and plan.stats["duplicate_identical_records"] == 1 and plan.conflicts[0]["kind"] == "duplicate_identical_record"
    assert apply_submission_details(hub, [plan]).inserted == 1


def test_source_hash_mismatch_is_not_ignored(tmp_path):
    body = json.dumps({"submissions": []}).encode()
    (tmp_path / "a.json").write_bytes(body)
    (tmp_path / "a.meta.json").write_text(json.dumps({"source_endpoint": SUBMISSIONS_URL, "fetched_at": "2026-09-24T07:21:24Z", "body_sha256": "0" * 64}), encoding="utf-8")
    with pytest.raises(SnapshotIntegrityError):
        load_qch_snapshot(tmp_path / "a.json")
    (tmp_path / "a.meta.json").write_text(json.dumps({"source_endpoint": SUBMISSIONS_URL, "fetched_at": "2026-09-24T07:21:24Z", "body_sha256": hashlib.sha256(body).hexdigest()}), encoding="utf-8")
    with pytest.raises(SnapshotIntegrityError):
        load_qch_snapshot(tmp_path / "a.json", expected_sha256="1" * 64)


# == idempotence, conflicts with stored rows, rollback ===========================================


def test_repeated_import_adds_and_updates_nothing(hub, tmp_path):
    snap = _snapshot(tmp_path, "a", [_record(U1, note=LONG_NOTE, claimedScore=5, coauthors=["x"]), _record(U4)])
    assert apply_submission_details(hub, [plan_submission_details(hub, snap)]).inserted == 2
    before = _rows(hub)
    again = plan_submission_details(hub, snap)
    assert again.stats["already_imported"] and apply_submission_details(hub, [again]).inserted == 0
    assert _rows(hub) == before  # not even imported_at changed


def test_changed_content_for_a_stored_body_raises_and_overwrites_nothing(hub, tmp_path):
    plan = plan_submission_details(hub, _snapshot(tmp_path, "a", [_record(U1, note="original")]))
    apply_submission_details(hub, [plan])
    before = _rows(hub)
    altered = [dataclasses.replace(plan.details[0], note="tampered", note_sha256=hashlib.sha256(b"tampered").hexdigest(), note_chars=8)]
    with pytest.raises(ValidationError, match="conflict with the stored import"):
        hub.submission_details.record(plan.source, altered)
    assert _rows(hub) == before


def test_failure_inside_the_transaction_rolls_back_the_source_and_every_row(hub, tmp_path):
    plan = plan_submission_details(hub, _snapshot(tmp_path, "a", [_record(U1, note="a"), _record(U2, note="b")]))
    bogus = SubmissionSourceDetail(**{**dataclasses.asdict(plan.details[-1]), "detail_id": detail_id(plan.source.detail_source_id, U5),
                                      "source_submission_uuid": U5, "submission_id": "no-such-submission"})
    with pytest.raises(ValidationError):  # FOREIGN KEY failure on the LAST row, after the source row and two detail rows
        hub.submission_details.record(plan.source, [*plan.details, bogus])
    assert _counts(hub, DETAIL_TABLES) == {t: 0 for t in DETAIL_TABLES}


# == the real store (temporary copy) =============================================================

PROTECTED_TABLES = [
    "submission", "submission_evaluation", "evaluation_snapshot", "circuit_version", "transformation_edge", "contribution",
    "contributor_identity", "contributor_alias", "contributor_import", "version_technique_label", "research_source",
    "research_population", "population_membership", "structural_metric", "artifact",
]


def _digest(conn, table):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    h = hashlib.sha256()
    for row in conn.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY {', '.join(cols)}"):
        h.update(json.dumps(list(row), default=str).encode())
    return h.hexdigest()


@pytest.fixture
def real_copy(tmp_path):
    if not REAL_DB.exists():
        pytest.skip("real store not present")
    db = tmp_path / "store.sqlite3"
    shutil.copy2(REAL_DB, db)
    h = QCH.open(str(db))
    conn = h._storage._conn  # the production store may already hold this import: clear it ON THIS COPY only
    conn.execute("DELETE FROM submission_source_detail")
    conn.execute("DELETE FROM submission_detail_source")
    conn.commit()
    yield h
    h.close()


@needs_real
def test_real_import_leaves_official_metrics_identities_labels_and_populations_unchanged(real_copy):
    conn = real_copy._storage._conn
    before = {t: _digest(conn, t) for t in PROTECTED_TABLES}
    snapshots = [load_qch_snapshot(REAL_SNAPSHOT, expected_sha256=REAL_SNAPSHOT_SHA256), load_pinned_supplement_snapshot(MANIFEST, PROJECT_ROOT)]
    plans = [plan_submission_details(real_copy, s) for s in snapshots]
    assert all(p.blocking == [] for p in plans)
    assert [(p.stats["source_records"], p.stats["matched_to_qch_submission"], p.stats["unmatched"]) for p in plans] == [(1248, 999, 249), (831, 607, 224)]
    assert apply_submission_details(real_copy, plans).inserted == 1248 + 831
    assert {t: _digest(conn, t) for t in PROTECTED_TABLES} == before  # official Q/T/S, submitters, contributions, 404 labels, both populations
    assert conn.execute("SELECT count(*) FROM version_technique_label").fetchone()[0] in (0, 404)
    assert conn.execute("SELECT count(*) FROM submission_source_detail_latest").fetchone()[0] == 1248
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert apply_submission_details(real_copy, [plan_submission_details(real_copy, s) for s in snapshots]).inserted == 0


@needs_real
def test_real_example_submission_note_matches_the_source_exactly(real_copy):
    uuid = "3ccbe24c-0429-4ea9-972a-80afb1421679"
    snapshot = load_qch_snapshot(REAL_SNAPSHOT, expected_sha256=REAL_SNAPSHOT_SHA256)
    apply_submission_details(real_copy, [plan_submission_details(real_copy, snapshot)])
    source = next(r for r in json.loads(snapshot.body)["submissions"] if r["id"] == uuid)
    stored = real_copy.submission_details.latest("ebb6a943f55c4497a7d0b39678813ff8")
    assert stored.source_submission_uuid == uuid and stored.note == source["note"] and stored.note_chars == len(source["note"]) == 8620
    assert stored.note_sha256 == hashlib.sha256(source["note"].encode("utf-8")).hexdigest()
    assert (stored.platform_created_at_raw, stored.platform_updated_at_raw) == ("2026-09-11T18:06:20.877Z", "2026-09-11T18:08:16.628Z")
    assert (stored.claimed_score_presence, stored.claimed_score_json, stored.coauthors_presence, stored.coauthors_json) == ("null", None, "empty", "[]")
    version = real_copy.versions.find_by_external_key("ecdsafail:9700396")
    assert version.realized_from_submission_id == "ebb6a943f55c4497a7d0b39678813ff8"
    assert [label for label in real_copy.research.list_technique_labels() if label.version_id == version.version_id] == []  # a note is not a label
    assert real_copy.evaluations.latest("ebb6a943f55c4497a7d0b39678813ff8").official_score == 1137367864  # untouched; claimedScore is null
