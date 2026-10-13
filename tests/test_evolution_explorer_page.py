"""Version Evolution Explorer page, driven headlessly with Streamlit's AppTest.

Two kinds of store are used, both TEMPORARY: a small synthetic fixture (tests/explorer_fixtures.py) for details, filters and
missing-table behaviour, and a copy of the real store (conftest `real_store_copy`) for the eight examples. The page only
ever opens them read-only.

What AppTest cannot do is click inside the custom SVG component; the click -> action logic is the pure
`interpret_graph_event` (tested below) and the live click path was exercised in a real browser (see
docs/QCH_VERSION_EVOLUTION_EXPLORER.md). Everything else runs through the real page script.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import explorer_fixtures as fx  # noqa: E402
from evolution_explorer_ui import MINUS, interpret_graph_event, md_escape  # noqa: E402
from qch.explorer import EvolutionExplorer  # noqa: E402
from qch.explorer.examples import EXAMPLES, ExampleSpec, Expectation  # noqa: E402
from qch.storage.sqlite.readonly import open_read_only  # noqa: E402

PAGE = "pages/Version_Evolution_Explorer.py"
SEARCH_BUTTON = "vee_search_btn"
KEYS = [spec.key for spec in EXAMPLES]


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    path = tmp_path_factory.mktemp("explorer_page") / "store.sqlite3"
    return path, fx.build_explorer_db(path)


def open_page(monkeypatch, db_path) -> AppTest:
    monkeypatch.setenv("QCH_DB_PATH", str(db_path))
    at = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=120)
    at.run()
    at.switch_page(PAGE).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def search(at: AppTest, query: str) -> AppTest:
    at.text_input(key="vee_query").input(query)
    at.button(key=SEARCH_BUTTON).click().run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def open_example(at: AppTest, key: str) -> AppTest:
    at.selectbox(key="vee_example_pick").select(key)
    at.button(key="vee_open_example").click().run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def select(at: AppTest, kind: str, identifier: str) -> AppTest:
    at.session_state["vee_selected"] = {"type": kind, "id": identifier}
    at.session_state["vee_edge_pick"] = identifier if kind == "edge" else ""  # what the page's own selection callbacks do
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def texts(at: AppTest) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption] + [t.value for t in at.text] + [i.value for i in at.info] + [w.value for w in at.warning]
    parts += [f"{m.label}: {m.value}" for m in at.metric]
    return "\n".join(str(p) for p in parts)


def tab(at: AppTest, label: str):
    return next(t for t in at.tabs if t.label == label)


def tab_text(at: AppTest, label: str) -> str:
    t = tab(at, label)
    return "\n".join(str(x.value) for kind in (t.markdown, t.caption, t.code) for x in kind)


def cards(at: AppTest) -> dict[str, str]:
    return {m.label: m.value for m in at.metric}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_note(db_path, key: str) -> str:
    with EvolutionExplorer(open_read_only(db_path)) as explorer:
        (match,) = [m for m, _ in explorer.search(key).matches if m.external_version_key == key]
        return next(d for d in explorer.version_detail(match.version_id).source_details if d.is_latest).note


# == start-up and compact layout ==================================================================


def test_a_missing_database_shows_the_error_and_configuration_guidance(monkeypatch, tmp_path):
    at = open_page(monkeypatch, tmp_path / "nope.sqlite3")
    assert any("QCH database not found" in e.value for e in at.error)
    assert "QCH_DB_PATH" in texts(at) and len(at.text_input) == 0  # the page stops before offering a search


def test_the_page_starts_compact_with_one_instruction_and_a_read_only_indicator(monkeypatch, store):
    path, _ = store
    at = open_page(monkeypatch, path)
    assert at.session_state["vee_state"] is None
    assert [b.key for b in at.button] == ["vee_search_btn", "vee_open_example"]  # one search row, one examples row; nothing else competes for space
    assert any(path.name in c.value and "read-only" in c.value for c in at.caption)  # a small visible indicator of the database and read-only access
    instruction = "Click ‘Open example’ to explore this version, or search for another version."
    assert [c.value for c in at.caption].count(instruction) == 1 and len(at.info) == 0  # one compact line, no banner
    assert [t.value for t in at.title] == ["Version Evolution Explorer"]
    assert len(at.tabs) == 0 and len(at.metric) == 0  # no details before something is selected


def test_search_field_has_a_visible_label_a_short_placeholder_and_the_examples_in_help(monkeypatch, store):
    at = open_page(monkeypatch, store[0])
    field = at.text_input(key="vee_query")
    assert (field.label, field.placeholder) == ("Search version", "Version ID, commit hash, or label")
    assert field.help.startswith("Examples: ecdsafail:31f9c58, 31f9c58, or V1.")


def test_the_data_source_details_panel_is_gone_and_the_instruction_disappears_once_a_graph_is_open(monkeypatch, real_store_copy):
    at = open_page(monkeypatch, real_store_copy)
    assert "Data source details" not in [e.label for e in at.expander]
    open_example(at, "ecdsafail:31f9c58")
    assert "Data source details" not in [e.label for e in at.expander] and str(real_store_copy) not in texts(at)
    assert not any("Open example’ to explore" in c.value for c in at.caption)
    assert any(t.label == "Provenance" for t in at.tabs)  # the record-level provenance stays


def test_the_page_css_clears_the_fixed_header_and_sets_the_larger_readable_type(monkeypatch, store):
    import re

    at = open_page(monkeypatch, store[0])
    css = next(m.value for m in at.markdown if m.value.lstrip().startswith("<style>"))
    top = float(re.search(r'stMainBlockContainer"\] \{ padding-top: ([0-9.]+)rem', css).group(1))
    assert top >= 3.9  # Streamlit's fixed header is about 3.75rem tall: less padding hides the top of the title behind it
    assert re.search(r"\.st-key-vee_title h1 \{[^}]*font-size: 30px", css) and "margin: 0" in css
    assert "font-size: 18px" in css and "[data-baseweb=\"tab\"]" in css and "font-size: 17px" in css and "font-size: 16px" in css
    assert "opacity: 1" in css and ":focus-visible" in css  # captions are not faded; keyboard focus stays visible
    assert css.count("<style>") == 1 and "html" not in css and ":root" not in css  # nothing global


def test_the_page_css_is_scoped_to_the_page_and_does_not_touch_the_theme(monkeypatch, store):
    at = open_page(monkeypatch, store[0])
    css = next(m.value for m in at.markdown if m.value.lstrip().startswith("<style>"))
    assert ".st-key-vee_open_example" in css and ".st-key-vee_expand" in css and "#0969da" in css  # neutral blue actions, by widget key
    assert "body" not in css and "primaryColor" not in css and "--primary" not in css  # no global theme change


# == search (synthetic store) =====================================================================


def test_an_exact_search_opens_the_one_hop_neighborhood_and_selects_the_searched_version(monkeypatch, store):
    path, ids = store
    at = search(open_page(monkeypatch, path), "demo:bbbb222")
    state = at.session_state["vee_state"]
    assert state.origin == ids["b"] and len(state.nodes) == 5 and len(state.edges) == 5  # b plus a, c, zero, nomet
    assert at.session_state["vee_selected"] == {"type": "node", "id": ids["b"]}
    assert "5 nodes · 5 edges" in texts(at) and any(m.value == "#### bbbb222" for m in at.markdown)


def test_a_commit_fragment_with_one_match_does_not_need_a_choice(monkeypatch, store):
    at = search(open_page(monkeypatch, store[0]), "bbbb222")
    assert at.session_state["vee_state"] is not None and any("One match" in c.value for c in at.caption)


def test_an_ambiguous_identifier_lists_the_matches_and_waits_for_an_explicit_choice(monkeypatch, store):
    path, ids = store
    at = search(open_page(monkeypatch, path), "abc12")
    assert at.session_state["vee_state"] is None  # nothing was chosen on the user's behalf
    assert any("Choose one to explore" in i.value for i in at.info)
    candidates = at.selectbox(key="vee_candidate")
    assert candidates.value is None and len(candidates.options) == 2
    candidates.select(next(o for o in candidates.options if "abc1299" in o)).run()
    at.button(key="vee_explore").click().run()
    assert not at.exception and at.session_state["vee_state"].origin == ids["f"]


def test_no_match_and_too_short_queries_give_messages_not_errors(monkeypatch, store):
    at = search(open_page(monkeypatch, store[0]), "nonexistent99")
    assert at.session_state["vee_state"] is None and any("No version matches" in w.value or "No exact match" in w.value for w in at.warning)
    at = search(at, "ab")
    assert any("at least 3 characters" in w.value for w in at.warning)


# == examples: availability =======================================================================


def test_the_examples_selector_lists_eight_numbered_examples_with_a_one_line_description(monkeypatch, real_store_copy):
    at = open_page(monkeypatch, real_store_copy)
    picker = at.selectbox(key="vee_example_pick")
    assert len(picker.options) == 8 and picker.options[0].startswith("1. Mainline and rejected branches") and picker.options[7].startswith("8. Baseline and missing predecessor metrics")
    assert all(key in option for key, option in zip(KEYS, picker.options))
    assert not at.button(key="vee_open_example").disabled
    assert EXAMPLES[0].description in texts(at)  # the description of the selected example, as a single caption line


def test_examples_missing_from_the_database_are_marked_unavailable_and_cannot_be_opened(monkeypatch, store):
    at = open_page(monkeypatch, store[0])
    picker = at.selectbox(key="vee_example_pick")
    assert len(picker.options) == 8 and all("unavailable" in option for option in picker.options)
    assert at.button(key="vee_open_example").disabled and "not in the configured database" in texts(at)
    assert at.session_state["vee_state"] is None


def test_an_example_that_differs_from_its_description_is_flagged_but_can_be_opened(monkeypatch, real_store_copy, tmp_path):
    path = tmp_path / "edited.sqlite3"
    shutil.copy2(real_store_copy, path)
    conn = sqlite3.connect(str(path))
    conn.execute("UPDATE version_technique_label SET family = 'Something else' WHERE version_id = (SELECT version_id FROM circuit_version WHERE external_version_key = 'ecdsafail:0780f67')")
    conn.commit()
    conn.close()
    at = open_page(monkeypatch, path)
    at.selectbox(key="vee_example_pick").select("ecdsafail:0780f67").run()
    picker = at.selectbox(key="vee_example_pick")
    assert "differs from its description" in picker.options[KEYS.index(picker.value)] and "this database differs from the description" in texts(at)
    assert not at.button(key="vee_open_example").disabled
    at.button(key="vee_open_example").click().run()
    assert not at.exception and at.session_state["vee_state"] is not None


def test_a_custom_catalog_is_used_with_a_synthetic_store(monkeypatch, store):
    import qch.explorer.examples as examples

    custom = (ExampleSpec("demo:bbbb222", "Demo example", "The promoted step from aaaa111 with two author labels.",
                          Expectation(neighborhood=(5, 5))),)
    monkeypatch.setattr(examples, "EXAMPLES", custom)
    path, ids = store
    at = open_page(monkeypatch, path)
    assert at.selectbox(key="vee_example_pick").options == ["1. Demo example  ·  demo:bbbb222"]
    open_example(at, "demo:bbbb222")
    assert at.session_state["vee_state"].origin == ids["b"] and "Opened example “Demo example”." in texts(at)
    select(at, "edge", ids["e_ab_next"])
    assert cards(at)["ΔS · official score"] == f"{MINUS}12,000" and cards(at)["Score reduction"] == "12.0000%"


# == examples: opening ============================================================================


@pytest.mark.parametrize("key", KEYS)
def test_each_example_opens_from_a_clean_slate_with_its_version_as_root_and_selection(monkeypatch, real_store_copy, key):
    at = open_example(open_page(monkeypatch, real_store_copy), key)
    state = at.session_state["vee_state"]
    assert state.nodes[state.origin].external_version_key == key
    assert at.session_state["vee_selected"] == {"type": "node", "id": state.origin} and at.session_state["vee_edge_pick"] == ""
    assert at.text_input(key="vee_query").value == key and len(state.nodes) >= 3
    assert [t.label for t in at.tabs] == ["Overview", "Submission", "Techniques", "Artifacts", "Provenance"]
    assert list(cards(at)) == ["Q · peak qubits", "T · avg executed Toffoli", "S · official score"]


def test_example_1_shows_five_nodes_and_four_edges_and_no_suggested_edge(monkeypatch, real_store_copy):
    at = open_example(open_page(monkeypatch, real_store_copy), "ecdsafail:31f9c58")
    state = at.session_state["vee_state"]
    assert (len(state.nodes), len(state.edges)) == (5, 4) and "5 nodes · 4 edges" in texts(at)
    assert "vee_suggested" not in [b.key for b in at.button]
    assert cards(at) == {"Q · peak qubits": "1,150", "T · avg executed Toffoli": "1,284,776", "S · official score": "1,477,492,400"}


def test_example_2_suggested_edge_shows_the_advertised_change_and_no_invented_label(monkeypatch, real_store_copy):
    at = open_example(open_page(monkeypatch, real_store_copy), "ecdsafail:897dda2")
    at.button(key="vee_suggested").click().run()
    assert not at.exception and at.session_state["vee_selected"]["type"] == "edge"
    assert cards(at) == {"ΔQ · peak qubits": "+171", "ΔT · avg executed Toffoli": f"{MINUS}332,069", "ΔS · official score": f"{MINUS}218,966,453", "Score reduction": "14.8201%"}
    assert "No author-assigned label recorded for this edge." in tab_text(at, "Techniques")
    assert "Submitter note" in " ".join(e.label for e in at.expander) and "Promoted sequence" in texts(at)


def test_example_3_shows_the_score_and_the_complete_note_exactly(monkeypatch, real_store_copy):
    at = open_example(open_page(monkeypatch, real_store_copy), "ecdsafail:9700396")
    assert cards(at)["S · official score"] == "1,137,367,864" and "submitter **jackylee0424**" in texts(at)
    assert "Submitter note — 8,620 characters" in [e.label for e in at.expander]
    at.toggle[0].set_value(True).run()
    assert [c.value for c in tab(at, "Submission").code if len(c.value) > 5000] == [expected_note(real_store_copy, "ecdsafail:9700396")]
    assert "No author-assigned label recorded." in tab_text(at, "Techniques")


def test_example_4_keeps_the_author_label_and_the_submitter_statement_in_separate_tabs(monkeypatch, real_store_copy):
    at = open_example(open_page(monkeypatch, real_store_copy), "ecdsafail:0780f67")
    at.button(key="vee_suggested").click().run()
    techniques, submission = tab_text(at, "Techniques"), tab_text(at, "Submission")
    assert "Windowing" in techniques and md_escape("K2 transcript pair-compressor") in techniques and "Model: Claude Opus" not in techniques
    assert "Model: Claude Opus" in submission and "Windowing" not in submission and "K2 transcript" not in submission
    assert "Windowing" not in tab_text(at, "Comparison") and "Model: Claude Opus" not in tab_text(at, "Comparison")


def test_examples_5_to_7_advertise_changes_that_the_store_confirms(monkeypatch, real_store_copy):
    at = open_page(monkeypatch, real_store_copy)
    expected = {
        "ecdsafail:9bcc27d": ("0", f"{MINUS}263,736", "6.9215%", "Fusion", "boundary cswap-merge"),
        "ecdsafail:d19dbb5": (f"{MINUS}398", "+114,202", "11.9467%", "Register and Scratch Allocation", "in-place mutation"),
        "ecdsafail:81ff75b": ("0", f"{MINUS}8,123", None, "Uncompute Strategy", "measurement uncompute"),
    }
    for key, (dq, dt, percent, family, change) in expected.items():
        open_example(at, key)
        at.button(key="vee_suggested").click().run()
        c = cards(at)
        assert (c["ΔQ · peak qubits"], c["ΔT · avg executed Toffoli"]) == (dq, dt), key
        assert percent is None or c["Score reduction"] == percent
        assert md_escape(family) in tab_text(at, "Techniques") and md_escape(change) in tab_text(at, "Techniques")


def test_example_8_baseline_has_metrics_and_an_edge_without_computable_change(monkeypatch, real_store_copy):
    at = open_example(open_page(monkeypatch, real_store_copy), "ecdsafail:6f7c159")
    assert cards(at) == {"Q · peak qubits": "2,715", "T · avg executed Toffoli": "3,960,753", "S · official score": "10,753,444,395"}
    at.button(key="vee_suggested").click().run()
    assert not [label for label in cards(at) if label.startswith("Δ")] and "not recorded for both endpoints" in texts(at)  # unavailable, never zero


def test_switching_examples_resets_the_exploration_and_the_filters_but_keeps_the_node_limit_and_database(monkeypatch, real_store_copy):
    at = open_example(open_page(monkeypatch, real_store_copy), "ecdsafail:31f9c58")
    at.number_input(key="vee_max_nodes").set_value(40).run()
    at.radio(key="vee_direction").set_value("out").run()
    at.multiselect(key="vee_relations").unselect("branched_from").run()
    select(at, "node", at.session_state["vee_state"].origin)
    at.button(key="vee_expand").click().run()
    before_query = at.text_input(key="vee_query").value
    open_example(at, "ecdsafail:0780f67")
    state = at.session_state["vee_state"]
    assert state.nodes[state.origin].external_version_key == "ecdsafail:0780f67" and len(state.nodes) == 3  # a fresh one-hop neighborhood
    assert at.radio(key="vee_direction").value == "both" and set(at.multiselect(key="vee_relations").value) == set(at.session_state["vee_known_relations"])
    assert at.number_input(key="vee_max_nodes").value == 40  # the visible-node limit is preserved
    assert at.session_state["vee_selected"] == {"type": "node", "id": state.origin} and before_query != at.text_input(key="vee_query").value
    body = texts(at)
    assert "Filters were reset: direction Both, all relationship types." in body and "3 nodes · 2 edges" in body and "filtered" not in body
    assert "Expanded" not in body  # nothing of the previous exploration is left
    assert at.session_state["vee_example_active"]["key"] == "ecdsafail:0780f67"


def test_opening_an_example_without_active_filters_does_not_claim_a_reset(monkeypatch, real_store_copy):
    at = open_example(open_page(monkeypatch, real_store_copy), "ecdsafail:31f9c58")
    open_example(at, "ecdsafail:897dda2")
    assert "Filters were reset" not in texts(at) and "Opened example “More qubits, fewer Toffolis”." in texts(at)


def test_the_advertised_relationships_cannot_be_hidden_by_an_earlier_filter(monkeypatch, real_store_copy):
    at = open_example(open_page(monkeypatch, real_store_copy), "ecdsafail:31f9c58")
    at.multiselect(key="vee_relations").unselect("branched_from").unselect("historical_successor").run()
    assert "3 nodes · 2 edges" in texts(at)  # the rejected branches are hidden ...
    open_example(at, "ecdsafail:31f9c58")
    assert "5 nodes · 4 edges" in texts(at)  # ... until an example is opened again


def test_opening_examples_never_modifies_the_database(monkeypatch, real_store_copy, tmp_path):
    path = tmp_path / "copy.sqlite3"
    shutil.copy2(real_store_copy, path)
    before = sha(path)
    at = open_page(monkeypatch, path)
    for key in KEYS:
        open_example(at, key)
    assert sha(path) == before and not Path(str(path) + "-journal").exists()
    assert any(path.name in c.value for c in at.caption)  # the configured database is still the one in use


# == expansion, selection, reset, filters (synthetic store) =======================================


def test_expanding_a_neighbor_adds_to_the_graph_and_keeps_the_selection_and_positions(monkeypatch, store):
    path, ids = store
    at = search(open_page(monkeypatch, path), "demo:aaaa111")
    before = dict(at.session_state["vee_state"].positions)
    assert sorted(n.external_version_key for n in at.session_state["vee_state"].nodes.values()) == ["demo:aaaa111", "demo:bbbb222", "demo:cccc333"]
    select(at, "node", ids["b"])
    at.button(key="vee_expand").click().run()
    state = at.session_state["vee_state"]
    assert not at.exception and len(state.nodes) == 5 and len(state.edges) == 6  # + zero, nomet; + b->c, zero->b, nomet->b
    assert at.session_state["vee_selected"] == {"type": "node", "id": ids["b"]}  # the selection survives the expansion
    assert all(state.positions[n] == p for n, p in before.items())  # and no existing node moved
    assert "Expanded demo:bbbb222: +2 node(s), +3 edge(s)." in texts(at)
    at.button(key="vee_expand").click().run()  # expanding again is a no-op and says so
    assert len(at.session_state["vee_state"].nodes) == 5 and "Nothing new" in texts(at)


def test_reset_returns_to_the_searched_versions_one_hop_neighborhood(monkeypatch, store):
    path, ids = store
    at = search(open_page(monkeypatch, path), "demo:aaaa111")
    select(at, "node", ids["b"])
    at.button(key="vee_expand").click().run()
    assert len(at.session_state["vee_state"].nodes) == 5
    at.button(key="vee_reset").click().run()
    state = at.session_state["vee_state"]
    assert len(state.nodes) == 3 and state.origin == ids["a"] and at.session_state["vee_selected"] == {"type": "node", "id": ids["a"]}
    assert "Reset to the searched version" in texts(at)


def test_the_node_limit_control_caps_the_loaded_neighbors_and_says_what_was_left_out(monkeypatch, store):
    at = open_page(monkeypatch, store[0])
    search(at, "demo:hub0000")  # 30 neighbors, default limit 25
    assert len(at.session_state["vee_state"].nodes) == 25 and "6 neighbor(s) not loaded (node limit)" in texts(at)
    at.number_input(key="vee_max_nodes").set_value(60).run()
    at.button(key="vee_reset").click().run()  # a reset loads again under the raised limit
    assert len(at.session_state["vee_state"].nodes) == 31 and "not loaded (node limit)" not in texts(at)


def test_direction_and_relationship_filters_change_the_drawn_graph_without_losing_loaded_data(monkeypatch, store):
    at = search(open_page(monkeypatch, store[0]), "demo:aaaa111")
    assert "3 nodes · 3 edges" in texts(at) and "filtered" not in texts(at)
    at.radio(key="vee_direction").set_value("out").run()
    assert "2 nodes · 2 edges" in texts(at) and "filtered" in texts(at) and "hidden by the direction/relationship filter" in texts(at)
    at.radio(key="vee_direction").set_value("both").run()
    at.multiselect(key="vee_relations").unselect("branched_from").run()
    assert "3 nodes · 2 edges" in texts(at)  # the parallel branched_from edge is hidden; a, b and c stay connected
    at.multiselect(key="vee_relations").select("branched_from").run()
    assert "3 nodes · 3 edges" in texts(at) and len(at.session_state["vee_state"].edges) == 3  # nothing was deleted by filtering


def test_a_selected_node_that_a_filter_hides_falls_back_to_the_searched_version(monkeypatch, store):
    path, ids = store
    at = search(open_page(monkeypatch, path), "demo:aaaa111")
    select(at, "node", ids["c"])
    at.radio(key="vee_direction").set_value("out").run()  # c only reaches a through an incoming edge of a: hidden
    assert at.session_state["vee_selected"] == {"type": "node", "id": ids["a"]}


def test_the_edge_selector_and_the_selection_stay_synchronized(monkeypatch, store):
    path, ids = store
    at = search(open_page(monkeypatch, path), "demo:aaaa111")
    picker = at.selectbox(key="vee_edge_pick")
    assert picker.options[0].startswith("Inspect an edge") and "Promoted sequence" in " ".join(picker.options) and "Branch" in " ".join(picker.options)  # friendly names
    assert "next_promoted_commit" not in " ".join(picker.options)  # raw identifiers stay in tooltips and provenance
    picker.select(ids["e_ab_branch"]).run()
    assert at.session_state["vee_selected"] == {"type": "edge", "id": ids["e_ab_branch"]}
    select(at, "edge", ids["e_ab_next"])  # a graph click sets the selection; the selector follows
    assert at.selectbox(key="vee_edge_pick").value == ids["e_ab_next"]
    select(at, "node", ids["a"])
    assert at.selectbox(key="vee_edge_pick").value in ("", None) or at.selectbox(key="vee_edge_pick").options[0].startswith("Inspect")


# == node details =================================================================================


def test_node_details_lead_with_status_submitter_time_and_official_metric_cards(monkeypatch, store):
    path, ids = store
    at = search(open_page(monkeypatch, path), "demo:bbbb222")
    header = next(m.value for m in at.markdown if m.value.startswith(":green-badge[accepted]"))
    assert "submitter **alice**" in header and md_escape("2026-01-05 10:00:00.123") in header and "(platform time)" in header
    assert ids["b"] not in header and fx.UUID_B not in header  # internal identifiers are not in the headline
    assert cards(at) == {"Q · peak qubits": "110", "T · avg executed Toffoli": "800", "S · official score": "88,000"}
    assert [t.label for t in at.tabs] == ["Overview", "Submission", "Techniques", "Artifacts", "Provenance"]
    identifiers = "".join(m.value for e in at.expander if e.label == "Identifiers" for m in e.markdown)
    assert ids["b"] in identifiers and md_escape(fx.UUID_B) in identifiers  # long internal and external identifiers live in the collapsed section


def test_missing_metrics_stay_unavailable_and_structural_counts_are_not_substituted(monkeypatch, store):
    path, ids = store
    at = select(search(open_page(monkeypatch, path), "demo:bbbb222"), "node", ids["nomet"])
    assert not {"Q · peak qubits", "T · avg executed Toffoli", "S · official score"} & set(cards(at))  # no official cards, and no structural count dressed up as T
    at = search(at, "demo:dddd444")  # a version with no submission at all: no official metrics, and no other recorded metrics in this fixture
    assert "No metrics are recorded for this version." in texts(at) and "Q · peak qubits" not in cards(at)


def test_overview_states_utc_once_and_keeps_every_clock_distinct(monkeypatch, store):
    path, ids = store
    at = search(open_page(monkeypatch, path), "demo:bbbb222")
    table = next(m.value for m in tab(at, "Overview").markdown if m.value.startswith("| Event | Time (UTC) | Clock |"))
    assert table.count("UTC") == 1  # once, in the heading
    for event, clock in (("Platform submission time", "platform"), ("Platform last update", "platform"), ("Promotion completed", "platform"),
                         ("Git author date of the realization commit", "Git"), ("QCH stored this version", "QCH ingestion")):
        assert f"| {event} |" in table and f"| {clock} |" in table
    assert md_escape("2026-01-05 10:00:00.123") in table and md_escape("2026-01-02 00:00:00") in table  # the Git author date keeps its own (second) precision
    overview = tab_text(at, "Overview")
    assert "Stored rejection reason:** none recorded" in overview and "Logical circuit:** " + md_escape("demo_add") in overview


def test_rejection_reason_and_status_text_are_on_the_overview(monkeypatch, store):
    path, ids = store
    at = select(search(open_page(monkeypatch, path), "demo:bbbb222"), "node", ids["c"])
    assert ":red-badge[rejected]" in texts(at) and md_escape("score did not improve current best") in tab_text(at, "Overview")


def test_submission_tab_holds_identity_evidence_note_claimed_score_and_coauthors(monkeypatch, store):
    path, ids = store
    at = search(open_page(monkeypatch, path), "demo:bbbb222")
    body = tab_text(at, "Submission")
    assert "Submitter account:** alice" in body and "AUTHORITATIVE" in body and "solverAccountId" in body and "old_alice" in body.replace(chr(92), "")
    assert fx.NOTE_NEW in [c.value for c in tab(at, "Submission").code]  # verbatim: CRLF, unicode, <b>tags</b>, **markdown**
    assert "claimedScore:** 88000 (the submitter's claim, not an official score)" in body
    assert md_escape("bob the free-text coauthor") in body and "as declared; not linked to any account" in body
    assert "Declared coauthors" not in tab_text(at, "Overview") and fx.NOTE_NEW not in tab_text(at, "Overview")  # long-form evidence stays out of the Overview


def test_absent_null_and_empty_source_fields_get_concise_distinct_wording_and_raw_states(monkeypatch, store):
    path, ids = store
    at = select(search(open_page(monkeypatch, path), "demo:aaaa111"), "node", ids["a"])
    body = tab_text(at, "Submission")
    assert "Note:** explicit null in the source" in body and "claimedScore:** Not provided (explicit null)" in body and "Declared coauthors:** No coauthors declared" in body
    raw = next(j.value for j in tab(at, "Submission").json)
    assert '"state": "empty"' in raw and '"state": "null"' in raw and "[]" in raw.replace(" ", "").replace(chr(10), "")  # the raw [] and its state stay accessible
    at = select(at, "node", ids["c"])  # claimedScore and coauthors fields ABSENT in that snapshot
    assert "Not provided (field absent in this snapshot)" in tab_text(at, "Submission")


def test_older_snapshots_are_selectable_and_shown_on_their_own(monkeypatch, store):
    path, ids = store
    at = search(open_page(monkeypatch, path), "demo:bbbb222")
    picker = next(s for s in at.selectbox if s.key and s.key.endswith("_snapshot"))
    assert picker.options[0].startswith("latest") and len(picker.options) == 2
    assert "latest for this submission" in tab_text(at, "Submission")
    picker.select(picker.options[1]).run()
    body = tab_text(at, "Submission")
    assert "an older snapshot" in body and "claimedScore:** 90000" in body and "claimedScore:** 88000" not in body
    assert fx.NOTE_OLD in [c.value for c in tab(at, "Submission").code] and fx.NOTE_NEW not in [c.value for c in tab(at, "Submission").code]  # never merged across snapshots
    assert "Not provided (field absent in this snapshot)" in body  # the July body has no coauthors key at all


def test_a_long_note_starts_as_an_explicit_preview_and_the_toggle_shows_every_character(monkeypatch, store):
    path, ids = store
    at = select(search(open_page(monkeypatch, path), "demo:aaaa111"), "node", ids["c"])
    code = [c.value for c in at.code if c.value.startswith("line of text")]
    assert len(code) == 1 and len(code[0]) == 1500 and "Showing the first 1,500 of 5,200 characters" in texts(at)
    next(t for t in at.toggle if t.label == "Show full text").set_value(True).run()
    assert [c.value for c in at.code if c.value.startswith("line of text")] == [fx.LONG_NOTE] and "Showing the first" not in texts(at)


def test_a_note_with_leading_newline_indentation_and_trailing_whitespace_is_displayed_exactly(monkeypatch, store):
    path, ids = store
    at = select(search(open_page(monkeypatch, path), "demo:bbbb222"), "node", ids["nomet"])
    assert fx.TRICKY_NOTE in [c.value for c in at.code]  # st.code alone would have dropped the first and last newline


def test_the_note_is_untrusted_plain_text_never_markdown_or_html(monkeypatch, store):
    at = search(open_page(monkeypatch, store[0]), "demo:bbbb222")
    assert "<b>tags</b>" not in texts(at) and "last line" not in texts(at)  # no Markdown/HTML element carries the note text


def test_techniques_tab_shows_labels_with_provenance_or_a_concise_non_claim(monkeypatch, store):
    path, ids = store
    at = search(open_page(monkeypatch, path), "demo:bbbb222")
    body = tab_text(at, "Techniques")
    assert "Windowing" in body and "K2 pair compressor" in body and "Fusion" in body and md_escape("second_census.tex") in body and "author_final_primary_label" in body.replace(chr(92), "")
    assert fx.NOTE_NEW not in body and "claimedScore" not in body  # separate evidence
    at = select(at, "node", ids["a"])
    assert "No author-assigned label recorded." in tab_text(at, "Techniques") and "no technique" not in tab_text(at, "Techniques").lower()


def test_artifacts_and_provenance_tabs(monkeypatch, store):
    path, ids = store
    at = search(open_page(monkeypatch, path), "demo:bbbb222")
    assert list(tab(at, "Artifacts").dataframe[0].value.columns)[:3] == ["Format", "Type", "Recorded status"]
    assert "does not open, hash or locate" in tab_text(at, "Artifacts")
    provenance = tab_text(at, "Provenance")
    assert "Evaluation snapshot" in provenance and "QCH stored this version (ingestion)" in provenance and "Submitter-statement snapshots" in provenance
    at = select(at, "node", ids["a"])
    assert "No artifact records" in tab_text(at, "Artifacts")


# == edge details =================================================================================


def test_edge_details_lead_with_the_identifiers_then_the_four_change_cards(monkeypatch, store):
    path, ids = store
    at = select(search(open_page(monkeypatch, path), "demo:aaaa111"), "edge", ids["e_ab_next"])
    assert [m.value for m in at.markdown if m.value.startswith("#### ")][-1] == "#### aaaa111 → bbbb222"
    assert cards(at) == {"ΔQ · peak qubits": "+10", "ΔT · avg executed Toffoli": f"{MINUS}200", "ΔS · official score": f"{MINUS}12,000", "Score reduction": "12.0000%"}
    assert all(not m.delta for m in at.metric)  # no arrows, no green/red: a change is not judged good or bad on its own
    assert [t.label for t in at.tabs] == ["Comparison", "Techniques", "Submission", "Provenance"]
    assert any(m.value.startswith("**Promoted sequence**") for m in at.markdown)  # the friendly name; the raw identifier is in the tooltip and the Provenance tab
    assert "next_promoted_commit" in tab_text(at, "Provenance").replace(chr(92), "")


def test_edge_comparison_table_lists_source_target_and_change_with_exact_values(monkeypatch, store):
    path, ids = store
    at = select(search(open_page(monkeypatch, path), "demo:aaaa111"), "edge", ids["e_ab_next"])
    body = tab_text(at, "Comparison")
    assert "| **Q** qubits | 100 | 110 | +10 |" in body and f"| **T** Toffoli | 1,000 | 800 | {MINUS}200 |" in body and f"| **S** score | 100,000 | 88,000 | {MINUS}12,000 |" in body
    assert "structural Toffoli counts are never substituted" in body and "does not by itself establish Git parentage" in body


def test_edge_techniques_submission_and_provenance_stay_accessible_and_separate(monkeypatch, store):
    path, ids = store
    at = select(search(open_page(monkeypatch, path), "demo:aaaa111"), "edge", ids["e_ab_next"])
    techniques, submission, provenance = tab_text(at, "Techniques"), tab_text(at, "Submission"), tab_text(at, "Provenance")
    assert "Windowing" in techniques and "Fusion" in techniques and fx.NOTE_NEW not in techniques
    assert "Target submitter account:** alice" in submission and fx.NOTE_NEW in submission and "Windowing" not in submission
    assert ids["e_ab_next"] in provenance.replace(chr(92), "") and "ingestion time, not when the change happened" in provenance
    at = select(at, "edge", ids["e_ab_branch"])
    assert "No author-assigned label recorded for this edge." in tab_text(at, "Techniques")
    assert "stored description" in tab_text(at, "Provenance") and "**Branch**" in texts(at)


def test_edge_with_zero_or_missing_source_metrics_does_not_invent_numbers(monkeypatch, store):
    path, ids = store
    at = search(open_page(monkeypatch, path), "demo:bbbb222")
    zero = select(at, "edge", ids["e_zb"])
    assert cards(zero)["ΔS · official score"] == "+88,000" and cards(zero)["Score reduction"] == "unavailable"  # a zero source score: no percentage
    missing = select(zero, "edge", ids["e_nb"])
    assert not [label for label in cards(missing) if label.startswith("Δ")] and "not recorded for both endpoints" in texts(missing)


# == missing optional tables ======================================================================


@pytest.mark.parametrize("dropped, message", [
    (["contribution", "contributor_alias", "contributor_import", "contributor_identity"], "This database has no contributor tables."),
    (["version_technique_label", "research_source"], "This database has no technique-label tables."),
    (["submission_source_detail_latest", "submission_source_detail", "submission_detail_source"], "no submission source-detail tables"),
    (["submission_evaluation", "evaluation_snapshot"], "This database has no evaluation tables."),
])
def test_the_page_degrades_to_messages_when_optional_tables_are_absent(monkeypatch, tmp_path, dropped, message):
    path = tmp_path / "trimmed.sqlite3"
    ids = fx.build_explorer_db(path)
    fx.drop_optional_tables(path, dropped)
    at = select(search(open_page(monkeypatch, path), "demo:bbbb222"), "node", ids["b"])
    assert message in texts(at) + tab_text(at, "Submission") + tab_text(at, "Techniques") + tab_text(at, "Overview")
    assert "5 nodes · 5 edges" in texts(at)  # search and graph still work


# == read-only behaviour ==========================================================================


def test_using_the_page_never_modifies_the_database_even_with_a_pending_migration(monkeypatch, tmp_path):
    path = tmp_path / "pending.sqlite3"
    ids = fx.build_explorer_db(path)
    conn = sqlite3.connect(str(path))
    conn.execute("DELETE FROM schema_migrations WHERE version = 8")  # a store QCH.open() would migrate on open
    conn.commit()
    conn.close()
    before = sha(path)
    at = search(open_page(monkeypatch, path), "demo:aaaa111")
    select(at, "node", ids["b"])
    at.button(key="vee_expand").click().run()
    select(at, "edge", ids["e_ab_next"])
    at.button(key="vee_reset").click().run()
    assert not at.exception and sha(path) == before and not Path(str(path) + "-journal").exists()


# == graph events (pure) ==========================================================================

NODES, EDGES = {"n1", "n2"}, {"e1"}


def test_graph_events_become_actions_and_unknown_or_repeated_ones_are_ignored():
    assert interpret_graph_event({"type": "node", "id": "n1", "nonce": 1}, node_ids=NODES, edge_ids=EDGES).kind == "select_node"
    assert interpret_graph_event({"type": "edge", "id": "e1", "nonce": 2}, node_ids=NODES, edge_ids=EDGES).kind == "select_edge"
    expand = interpret_graph_event({"type": "expand", "id": "n2", "nonce": 3}, node_ids=NODES, edge_ids=EDGES)
    assert (expand.kind, expand.identifier, expand.nonce) == ("expand", "n2", 3)
    for bad in (None, {}, "text", {"type": "node", "id": "nope"}, {"type": "edge", "id": "n1"}, {"type": "zoom", "id": "n1"}):
        assert interpret_graph_event(bad, node_ids=NODES, edge_ids=EDGES).kind is None
    repeated = interpret_graph_event({"type": "expand", "id": "n2", "nonce": 3}, node_ids=NODES, edge_ids=EDGES, last_nonce=3)
    assert repeated.kind is None  # a double-click's second delivery of the same trigger value is not processed twice
