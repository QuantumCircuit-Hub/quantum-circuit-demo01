"""Version Evolution Explorer: metric deltas, timestamp display, graph payload and text formatting (pure)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import evolution_explorer_ui as ui  # noqa: E402
from qch.explorer import graph  # noqa: E402
from qch.explorer.metrics import official_comparison, percent_change, score_reduction_percent, structural_comparison  # noqa: E402
from qch.explorer.models import EdgeRecord, MetricRecord, OfficialMetrics, SearchResult, VersionSummary  # noqa: E402
from qch.explorer.service import parse_evidence  # noqa: E402
from qch.explorer.timefmt import format_utc  # noqa: E402


# == metric deltas ===============================================================================


def test_acceptance_case_31f9c58_to_897dda2_exact_deltas_and_percentage():
    source = OfficialMetrics(1150, 1284776, 1477492400)
    target = OfficialMetrics(1321, 952707, 1258525947)
    result = official_comparison(source, target)
    assert (result.q_delta, result.t_delta, result.s_delta) == (171, -332069, -218966453)
    assert result.score_reduction_percent == pytest.approx(14.8201, abs=5e-5)
    assert round(result.score_reduction_percent, 4) == 14.8201


def test_deltas_are_exact_integers_even_beyond_float_precision():
    big = 2**62
    result = official_comparison(OfficialMetrics(1, 1, big + 1), OfficialMetrics(1, 1, big))
    assert result.s_delta == -1 and isinstance(result.s_delta, int)


def test_missing_values_stay_unavailable_not_zero():
    partial = official_comparison(OfficialMetrics(10, None, 100), OfficialMetrics(12, 5, None))
    assert partial.q_delta == 2 and partial.t_delta is None and partial.s_delta is None and partial.score_reduction_percent is None
    assert official_comparison(OfficialMetrics(), OfficialMetrics(1, 2, 3)) is None  # one endpoint has no official metrics at all
    assert official_comparison(OfficialMetrics(1, 2, 3), OfficialMetrics()) is None


def test_zero_source_score_gives_an_unavailable_percentage_not_infinity():
    assert score_reduction_percent(0, 5) is None and score_reduction_percent(None, 5) is None and score_reduction_percent(5, None) is None
    zero = official_comparison(OfficialMetrics(0, 0, 0), OfficialMetrics(3, 4, 12))
    assert zero.s_delta == 12 and zero.score_reduction_percent is None
    assert score_reduction_percent(100, 100) == 0.0 and score_reduction_percent(100, 150) == -50.0  # an increase is a negative reduction


def test_structural_comparison_matches_by_name_and_never_merges_different_units():
    source = (MetricRecord("gate_count", 10.0, "gates"), MetricRecord("depth", 4.0), MetricRecord("only_source", 1.0))
    target = (MetricRecord("gate_count", 8.0, "gates"), MetricRecord("depth", 0.0), MetricRecord("only_target", 2.0))
    rows = {m.name: m for m in structural_comparison(source, target)}
    assert set(rows) == {"gate_count", "depth"}  # only metrics recorded for BOTH endpoints
    assert rows["gate_count"].delta == -2.0 and rows["gate_count"].percent_change == pytest.approx(-20.0) and rows["gate_count"].unit == "gates"
    assert percent_change(0, 5) is None and percent_change(None, 5) is None
    mismatch = structural_comparison((MetricRecord("x", 1.0, "a"),), (MetricRecord("x", 2.0, "b"),))[0]
    assert mismatch.unit is None


def test_evidence_json_is_parsed_or_returned_raw():
    assert parse_evidence('{"a": 1}') == {"a": 1} and parse_evidence("{}") == {} and parse_evidence(None) == {}
    assert parse_evidence("not json {") == "not json {"


# == timestamps ==================================================================================


def test_timezone_aware_timestamps_render_in_utc_with_the_stored_precision():
    assert format_utc("2026-09-11T18:06:20.877Z").text == "2026-09-11 18:06:20.877 UTC"
    assert format_utc("2026-08-20T07:30:42Z").text == "2026-08-20 07:30:42 UTC"
    assert format_utc("2026-09-21T06:47:35.808474+00:00").text == "2026-09-21 06:47:35.808474 UTC"
    assert format_utc("2026-09-21T09:47:35+03:00").text == "2026-09-21 06:47:35 UTC"  # other offsets are converted, not dropped
    assert format_utc("2026-09-11T18:06:20.877Z").raw == "2026-09-11T18:06:20.877Z"


def test_naive_missing_and_unparseable_timestamps_are_never_assumed_to_be_utc():
    naive = format_utc("2026-09-11 18:06:20")
    assert "timezone not stated" in naive.text and not naive.normalized and "UTC" not in naive.text.replace("timezone", "")
    assert format_utc(None).text == "unavailable" and format_utc("").text == "unavailable"
    assert "not an ISO-8601" in format_utc("yesterday").text


# == text formatting =============================================================================


def test_number_and_percent_formatting_keeps_signs_and_marks_unavailable():
    assert ui.fmt_int(1477492400) == "1,477,492,400" and ui.fmt_int(None) == "unavailable"
    assert ui.fmt_signed(171) == "+171" and ui.fmt_signed(-332069) == chr(0x2212) + "332,069" and ui.fmt_signed(0) == "0" and ui.fmt_signed(None) == "unavailable"
    assert ui.fmt_percent(14.82014) == "14.8201%" and ui.fmt_percent(None) == "unavailable"
    assert ui.fmt_number(3.0) == "3" and ui.fmt_number(2.5) == "2.5"


def test_markdown_escaping_keeps_untrusted_text_inert():
    text = "<script>alert(1)</script> **bold** _it_ [link](http://x) `code` | pipe # head"
    escaped = ui.md_escape(text)
    for raw in ("<script>", "**", "[link]", "`code`", "|"):
        assert raw not in escaped.replace("\\" + raw[0], "")  # every special character is preceded by a backslash
    assert ui.md_escape(None) == "" and ui.md_escape("plain-text") == "plain\\-text"


def test_long_notes_are_previewed_explicitly_and_shown_in_full_on_request():
    note = "x" * (ui.NOTE_PREVIEW_CHARS + 500)
    text, notice = ui.note_display(note, show_full=False)
    assert len(text) == ui.NOTE_PREVIEW_CHARS and f"{len(note):,}" in notice  # the truncation is stated, never silent
    assert ui.note_display(note, show_full=True) == (note, None)
    short = "short\nnote"
    assert ui.note_display(short, show_full=False) == (short, None) and not ui.is_long_note(short) and ui.is_long_note(note)
    assert ui.note_display(None, show_full=False) == ("", None)


@pytest.mark.parametrize("note", ["plain", "", "\n", "\nleading newline", "trailing newline\n", "\n\nboth\n\n", "  indented\n    more", "\ttab\r\nCRLF\r\n", "   ", pytest.param("x" * 50000, id="very-long")])
def test_verbatim_code_body_survives_streamlits_one_newline_trim_unchanged(note):
    import re

    body = ui.verbatim_code_body(note)
    shown = re.sub(r"\n\Z", "", re.sub(r"\A\n", "", body))  # the exact rule of streamlit/elements/code.py (st.code)
    assert shown == note


def test_presence_states_have_distinct_wording():
    words = {ui.presence_text(p) for p in ("value", "null", "empty", "absent")}
    assert len(words) == 4 and "explicit null" in ui.presence_text("null") and "absent" in ui.presence_text("absent")


def test_database_path_follows_the_project_convention(monkeypatch, tmp_path):
    monkeypatch.delenv("QCH_DB_PATH", raising=False)
    assert ui.explorer_db_path() == ui.PROJECT_ROOT / "data" / "qch_ecdsa_p17.sqlite3"
    monkeypatch.setenv("QCH_DB_PATH", "some/relative.sqlite3")
    assert ui.explorer_db_path() == ui.PROJECT_ROOT / "some" / "relative.sqlite3"
    monkeypatch.setenv("QCH_DB_PATH", str(tmp_path / "abs.sqlite3"))
    assert ui.explorer_db_path() == tmp_path / "abs.sqlite3"


# == graph payload ===============================================================================


def _view():
    versions = {n: VersionSummary(version_id=n, logical_circuit_id="c", external_version_key=f"demo:{n}", platform_status=s,
                                  official=OfficialMetrics(1, 2, 2) if s else OfficialMetrics())
                for n, s in (("a", "accepted"), ("b", "rejected"), ("c", None))}
    edges = [EdgeRecord("e1", "a", "b", "next_promoted_commit"), EdgeRecord("e2", "a", "b", "branched_from"),
             EdgeRecord("e3", "b", "c", "historical_successor"), EdgeRecord("e4", "c", "c", "weird_relation")]

    class Provider:
        def get_versions(self, ids):
            return {i: versions[i] for i in ids}

        def edges_for(self, version_id, direction, relation_types):
            return [e for e in edges if version_id in (e.source_version_id, e.target_version_id)]

    state = graph.start(Provider(), "b")
    graph.expand(state, Provider(), "c")
    return graph.project(state)


def test_payload_encodes_status_text_relation_styles_and_origin_and_selection():
    view = _view()
    payload = ui.build_graph_payload(view, selected={"type": "node", "id": "a"}, bounds=ui.bounds_of(view.positions))
    nodes = {n["id"]: n for n in payload["nodes"]}
    assert {n["status"] for n in payload["nodes"]} == {"accepted", "rejected", "status n/a"}  # colors always come with readable text
    assert nodes["b"]["origin"] and not nodes["a"]["origin"] and nodes["a"]["selected"] and not nodes["b"]["selected"]
    dashes = {e["relation"]: e["dash"] for e in payload["edges"]}
    assert dashes == {"next_promoted_commit": "solid", "branched_from": "dashed", "historical_successor": "dotted", "weird_relation": "dashdot"}
    parallel = [e for e in payload["edges"] if {e["source"], e["target"]} == {"a", "b"}]
    assert sorted((e["slot"], e["slots"]) for e in parallel) == [(0, 2), (1, 2)]  # parallel edges fan out instead of overlapping
    assert {r["label"] for r in payload["legend"]["relations"]} >= {"Promoted sequence (solid)", "Branch (dashed)", "weird_relation (dash-dot)"}
    assert {x["text"] for x in payload["legend"]["statuses"]} == {n["status"] for n in payload["nodes"]}  # the legend lists the statuses that are drawn, as text and color


def test_payload_selection_of_an_edge_and_stable_coordinates_under_growing_bounds():
    view = _view()
    bounds = ui.bounds_of(view.positions)
    first = ui.build_graph_payload(view, selected={"type": "edge", "id": "e3"}, bounds=bounds)
    assert [e["id"] for e in first["edges"] if e["selected"]] == ["e3"] and not any(n["selected"] for n in first["nodes"])
    grown = bounds.grow(ui.Bounds(bounds.min_col, bounds.max_col + 3, bounds.min_row - 2, bounds.max_row + 2))
    second = ui.build_graph_payload(view, selected=None, bounds=grown)
    assert grown.min_col == bounds.min_col and grown.min_row < bounds.min_row
    # growing to the right never moves a node horizontally; growing upward shifts every node by the same amount (the canvas origin)
    assert {n["id"]: n["x"] for n in first["nodes"]} == {n["id"]: n["x"] for n in second["nodes"]}
    assert len({second_n["y"] - first_n["y"] for first_n, second_n in zip(first["nodes"], second["nodes"])}) == 1


def test_payload_with_focus_and_empty_view_is_well_formed():
    view = _view()
    payload = ui.build_graph_payload(view, selected=None, bounds=ui.bounds_of(view.positions), focus={"id": "a", "seq": 7})
    assert payload["focus"] == {"id": "a", "seq": 7} and payload["width"] >= ui.MIN_CANVAS_W and payload["height"] >= ui.MIN_CANVAS_H


def test_search_table_rows_show_metrics_or_unavailable():
    summary = VersionSummary("v1", "c", "demo:a", platform_status="accepted", official=OfficialMetrics(10, 20, 200), logical_circuit_name="add")
    bare = VersionSummary("v2", "c", None, version_label="V1")
    rows = ui.search_rows(SearchResult("q", ((summary, "external_version_key"), (bare, "version_label"))))
    assert rows[0]["S (official score)"] == "200" and rows[0]["Logical circuit"] == "add"
    assert rows[1]["Version"] == "V1" and rows[1]["Platform status"] == "unavailable" and rows[1]["Q (peak qubits)"] == "unavailable"
    assert "demo:a" in ui.search_option_label(summary) and "v1"[:2] in ui.search_option_label(summary)
