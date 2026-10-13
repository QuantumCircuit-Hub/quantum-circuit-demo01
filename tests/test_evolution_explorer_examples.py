"""Version Evolution Explorer: the eight examples, friendly relation names and the read-only "lowest score" query.

Real-data checks use a TEMPORARY COPY of the store (conftest `real_store_copy`) opened through the explorer's read-only
path; tests that need a changed database edit a private copy of that copy. Nothing touches the production file.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import explorer_fixtures as fx  # noqa: E402
from qch.explorer import EvolutionExplorer, relation_label  # noqa: E402
from qch.explorer.examples import EXAMPLES, STATE_AMBIGUOUS, STATE_AVAILABLE, STATE_DIFFERS, STATE_UNAVAILABLE, check_example, check_examples  # noqa: E402
from qch.explorer.models import VersionSummary  # noqa: E402
from qch.explorer.timefmt import display_bare, format_utc  # noqa: E402
from qch.storage.sqlite.readonly import open_read_only  # noqa: E402

EXPECTED = [
    ("ecdsafail:31f9c58", "Mainline and rejected branches"),
    ("ecdsafail:897dda2", "More qubits, fewer Toffolis"),
    ("ecdsafail:9700396", "Best score in the local accepted records"),
    ("ecdsafail:0780f67", "Windowing technique"),
    ("ecdsafail:9bcc27d", "Fusion with a large Toffoli reduction"),
    ("ecdsafail:d19dbb5", "Fewer qubits with more Toffolis"),
    ("ecdsafail:81ff75b", "Measurement-based uncompute"),
    ("ecdsafail:6f7c159", "Baseline and missing predecessor metrics"),
]


@pytest.fixture
def real(real_store_copy):
    with EvolutionExplorer(open_read_only(real_store_copy)) as explorer:
        yield explorer


def edited_copy(real_store_copy, tmp_path, sql: str, *params):
    path = tmp_path / "edited.sqlite3"
    shutil.copy2(real_store_copy, path)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()
    return path


# == catalog ======================================================================================


def test_the_catalog_has_the_eight_examples_in_order_with_their_titles():
    assert [(s.key, s.title) for s in EXAMPLES] == EXPECTED
    assert len({s.key for s in EXAMPLES}) == 8


def test_descriptions_are_one_short_sentence_and_carry_no_hard_coded_metric_values():
    for spec in EXAMPLES:
        assert 20 < len(spec.description) < 240 and spec.description.count(". ") == 0, spec.key
        assert not re.search(r"\b\d{1,3}(,\d{3})+\b|%|\b\d{4,}\b", spec.description)  # standalone numbers only; commit ids like cc5584e are identifiers, f"{spec.key}: numbers belong in the database, not the catalog"
    assert EXAMPLES[2].fallback[0] == "Long submitter note and a large neighborhood"  # used if the best-score claim stops holding


# == verification against the store ===============================================================


def test_all_eight_examples_are_verified_against_the_real_store(real):
    statuses = check_examples(real)
    assert [(s.spec.key, s.state, s.problems) for s in statuses] == [(key, STATE_AVAILABLE, ()) for key, _ in EXPECTED]
    assert [s.title for s in statuses] == [title for _, title in EXPECTED]  # the best-score claim holds, so the original title stays
    with_edge = {s.spec.key: s.suggested_edge_source for s in statuses}
    assert with_edge == {"ecdsafail:31f9c58": None, "ecdsafail:897dda2": "31f9c58", "ecdsafail:9700396": None, "ecdsafail:0780f67": "cc5584e",
                         "ecdsafail:9bcc27d": "1c400f2", "ecdsafail:d19dbb5": "3bbccd3", "ecdsafail:81ff75b": "e33b400", "ecdsafail:6f7c159": "ca1bf33"}


def test_example_verification_reads_the_database_it_does_not_contain_the_numbers(real):
    (status,) = check_examples(real, (EXAMPLES[1],))
    detail = real.edge_detail(status.suggested_edge_id)
    assert (detail.official.q_delta, detail.official.t_delta, detail.official.s_delta) == (171, -332069, -218966453)  # from the store


def test_examples_are_unavailable_when_the_database_does_not_contain_them(tmp_path):
    path = tmp_path / "fixture.sqlite3"
    fx.build_explorer_db(path)
    with EvolutionExplorer(open_read_only(path)) as explorer:
        statuses = check_examples(explorer)
    assert {s.state for s in statuses} == {STATE_UNAVAILABLE} and all(not s.openable and s.version_id is None for s in statuses)
    assert "not in this database" in statuses[0].problems[0]


def test_an_example_whose_description_no_longer_matches_is_flagged_but_still_openable(real_store_copy, tmp_path):
    path = edited_copy(real_store_copy, tmp_path, "UPDATE version_technique_label SET family = 'Something else' WHERE version_id ="
                       " (SELECT version_id FROM circuit_version WHERE external_version_key = 'ecdsafail:0780f67')")
    with EvolutionExplorer(open_read_only(path)) as explorer:
        statuses = {s.spec.key: s for s in check_examples(explorer)}
    changed = statuses["ecdsafail:0780f67"]
    assert changed.state == STATE_DIFFERS and changed.openable and any("Windowing" in p for p in changed.problems)
    assert {s.state for k, s in statuses.items() if k != "ecdsafail:0780f67"} == {STATE_AVAILABLE}  # the others are untouched


def test_a_changed_official_metric_is_caught_by_the_delta_check(real_store_copy, tmp_path):
    path = edited_copy(real_store_copy, tmp_path, "UPDATE submission_evaluation SET official_peak_qubits = official_peak_qubits + 5 WHERE submission_id ="
                       " (SELECT realized_from_submission_id FROM circuit_version WHERE external_version_key = 'ecdsafail:31f9c58')")
    with EvolutionExplorer(open_read_only(path)) as explorer:
        status = next(s for s in check_examples(explorer) if s.spec.key == "ecdsafail:897dda2")
    assert status.state == STATE_DIFFERS and any("ΔQ" in p for p in status.problems)


def test_the_best_score_title_falls_back_when_another_accepted_submission_scores_lower(real_store_copy, tmp_path):
    path = edited_copy(real_store_copy, tmp_path, "UPDATE submission_evaluation SET official_score = 1000 WHERE submission_id ="
                       " (SELECT realized_from_submission_id FROM circuit_version WHERE external_version_key = 'ecdsafail:a39e07e')")
    with EvolutionExplorer(open_read_only(path)) as explorer:
        status = next(s for s in check_examples(explorer) if s.spec.key == "ecdsafail:9700396")
    assert status.title == "Long submitter note and a large neighborhood" and "live leaderboard" not in status.description
    assert status.state == STATE_AVAILABLE and status.problems == ()  # only the superlative was dropped; the note and the neighborhood are still true


def test_an_identifier_matching_several_versions_is_reported_as_ambiguous():
    summary = lambda n: VersionSummary(version_id=f"v{n}", logical_circuit_id="c", external_version_key="ecdsafail:31f9c58")  # noqa: E731

    class Twin:
        def search(self, query, logical_circuit_id=None, limit=25):
            from qch.explorer.models import SearchResult

            return SearchResult(query, ((summary(1), "external_version_key"), (summary(2), "external_version_key")))

    status = check_example(Twin(), EXAMPLES[0])
    assert status.state == STATE_AMBIGUOUS and status.openable and "2 versions" in status.problems[0]


# == read-only lowest score =======================================================================


def test_lowest_official_score_query_on_the_real_store_and_on_the_fixture(real, tmp_path):
    best = real.lowest_official_score("accepted")
    assert best.external_version_key == "ecdsafail:9700396" and best.official.score == 1137367864
    assert real.lowest_official_score("rejected").platform_status == "rejected" and real.lowest_official_score("no-such-status") is None
    path = tmp_path / "fixture.sqlite3"
    fx.build_explorer_db(path)
    with EvolutionExplorer(open_read_only(path)) as explorer:
        assert explorer.lowest_official_score("accepted").external_version_key == "demo:zero000"  # score 0 is a value, not missing
        assert explorer.lowest_official_score("rejected").external_version_key == "demo:cccc333"
    fx.drop_optional_tables(path, ["submission_evaluation", "evaluation_snapshot"])
    with EvolutionExplorer(open_read_only(path)) as explorer:
        assert explorer.lowest_official_score("accepted") is None


# == friendly relation names and bare times =======================================================


def test_friendly_relation_names_keep_unknown_types_under_their_raw_identifier():
    assert relation_label("next_promoted_commit") == "Promoted sequence"
    assert relation_label("branched_from") == "Branch"
    assert relation_label("historical_successor") == "Historical sequence"
    assert relation_label("optimizes") == "optimizes"  # no invented name


def test_bare_times_drop_the_utc_suffix_only_when_the_value_really_is_utc():
    assert display_bare("2026-08-20T07:30:16.707Z") == "2026-08-20 07:30:16.707"
    assert display_bare("2026-09-21T09:47:35+03:00") == "2026-09-21 06:47:35"  # converted, and only the suffix is omitted
    assert format_utc("2026-08-20T07:30:16.707Z").text.endswith(" UTC")  # the full form still says UTC
    assert "timezone not stated" in display_bare("2026-09-11 18:06:20")  # never presented as UTC
    assert display_bare(None) == "unavailable"
