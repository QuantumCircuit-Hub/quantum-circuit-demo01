"""Ask QCH -- a local, chat-style natural-language interface over the
REAL QCH pipeline:

    question -> Qwen2.5-14B planner -> validator -> SemanticGuard
             -> clarification / SAFE_REPAIR -> QCHQueryExecutor
             -> real QCH database -> ResultSanityChecker
             -> coverage-aware grounded answer

This page is a THIN presentation layer over the existing
`qch.nl.service.QCHNaturalLanguageService` (Phase 2B/2C, frozen) -- it
collects input, maintains UI/session state, calls the service, and
displays the structured result it returns. No query semantics live
here; see `src/ask_qch_config.py` (config + health checks) and
`src/ask_qch_presentation.py` (pure formatting helpers, no
`streamlit` import, unit-tested separately).

Local-only (Phase 2D): read-only against the real database, no public
deployment. Run with `streamlit run app.py` from the project root and
open "Ask QCH" from the sidebar.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ask_qch_config import EXAMPLE_QUESTIONS, AskQCHConfig, DisabledPlannerBackend, load_config, run_health_checks  # noqa: E402
from ask_qch_presentation import (  # noqa: E402
    clarification_captions,
    clarification_choices,
    format_contributor_attribution,
    format_contributor_summary,
    format_coverage_line,
    format_latency_fields,
    format_plan_sections,
    format_provenance_fields,
    format_raw_result_rows,
    format_unresolved_notice,
    status_icon_and_label,
)
from qch import QCH  # noqa: E402
from qch.nl.llm_backend import LocalModelBackend, LocalModelBackendConfig, OpenAICompatibleClient, RetryingInferenceClient  # noqa: E402
from qch.nl.service import ConversationContext, QCHNaturalLanguageService, SystemStatus  # noqa: E402
from qch_navigation import ASK_PAGE_TITLE, render_main_navigation  # noqa: E402

st.set_page_config(page_title=f"{ASK_PAGE_TITLE} · QCH", page_icon="\U0001f4ac", layout="wide")
render_main_navigation()

OTHER_LABEL = "Other (type my own)"



# -- resource setup --------------------------------------------------------------
#
# A fresh `QCH` (SQLite-backed) hub is opened and closed WITHIN each
# single call to `_process_input`, never cached/shared across reruns.
# `sqlite3` connections are not safe to share across threads
# (`check_same_thread=True` is the driver's own default, which
# `qch.storage.sqlite.backend.SQLiteStorage.open` relies on and this
# UI must not second-guess), and Streamlit's ScriptRunner does not
# guarantee a session's reruns execute on the same OS thread -- caching
# the hub via `st.cache_resource` produced a real, reproducible
# `sqlite3.ProgrammingError` ("...created in a thread can only be used
# in that same thread...") during local testing of this exact page.
# Opening a small local SQLite file is cheap (milliseconds) relative to
# the LLM planning latency (seconds) this page already waits on, so
# this trades a negligible amount of performance for correctness rather
# than touching any frozen backend code.


def _build_service(config: AskQCHConfig) -> QCHNaturalLanguageService:
    hub = QCH.open(str(config.db_path))
    if not config.llm_enabled:
        # Public demo: the LLM planner is never contacted; deterministic routes are unchanged.
        return QCHNaturalLanguageService(DisabledPlannerBackend(), hub, data_source_label=str(config.db_path), model_name="llm_planner_disabled", compositional_routing=True)
    backend = LocalModelBackend(
        LocalModelBackendConfig(
            endpoint=config.llm_base_url,
            model_name=config.llm_model,
            timeout=config.llm_timeout_seconds,
            temperature=0.0,
            seed=0,
            prompt_version=config.prompt_version,
        ),
        client=RetryingInferenceClient(OpenAICompatibleClient()),
    )
    return QCHNaturalLanguageService(backend, hub, data_source_label=str(config.db_path), model_name=config.llm_model, compositional_routing=True)


# -- session state -------------------------------------------------------------

if "ask_qch_messages" not in st.session_state:
    st.session_state.ask_qch_messages = []  # list of {"role": "user"|"assistant", "content": str, "result": ServiceResult|None}
if "ask_qch_conversation_context" not in st.session_state:
    st.session_state.ask_qch_conversation_context = None
if "ask_qch_last_result" not in st.session_state:
    st.session_state.ask_qch_last_result = None


def _reset_conversation() -> None:
    st.session_state.ask_qch_messages = []
    st.session_state.ask_qch_conversation_context = None
    st.session_state.ask_qch_last_result = None


def _process_input(config: AskQCHConfig, text: str) -> None:
    """The ONE path every question goes through -- example buttons,
    clarification choices, and free-text chat input all call this same
    function (spec: "do not create special execution paths for
    examples"). Never touches query semantics itself; only opens a
    fresh service (see `_build_service`), calls `service.ask()`, and
    records the result."""
    ctx = st.session_state.ask_qch_conversation_context
    st.session_state.ask_qch_messages.append({"role": "user", "content": text, "result": None})
    try:
        with st.spinner("Thinking..."):
            service = _build_service(config)
            try:
                result = service.ask(text, conversation_context=ctx)
            finally:
                service.hub.close()
    except Exception as exc:  # noqa: BLE001 -- must never crash the whole app; shown as a generic message + local-only developer detail
        st.session_state.ask_qch_messages.append(
            {"role": "assistant", "content": "Something went wrong while processing this question.", "result": None, "error_detail": f"{type(exc).__name__}: {exc}"}
        )
        st.session_state.ask_qch_conversation_context = None
        return
    st.session_state.ask_qch_messages.append({"role": "assistant", "content": result.answer, "result": result, "error_detail": None})
    st.session_state.ask_qch_conversation_context = result.conversation_context
    st.session_state.ask_qch_last_result = result


# -- page header -----------------------------------------------------------------

st.title("QCH — Quantum Circuit Hub")
st.header(ASK_PAGE_TITLE)
st.write("Ask natural-language questions about quantum circuits, versions, metrics, transformations, and evolution stored in QCH.")

with st.container(border=True):
    st.markdown("**About the dataset — ECDSA.Fail Challenge**")
    st.markdown(
        "This demo uses real circuit-evolution data from the ECDSA.Fail Challenge, an open optimization challenge for "
        "reversible secp256k1 point-addition circuits used in Shor’s algorithm. Participants iteratively submitted and "
        "improved quantum circuits, producing a rich history of submissions, versions, branches, contributors, and "
        "evaluation results.\n\n"
        "The challenge evaluates circuits using peak logical qubits **Q** and average executed Toffoli count **T**, with "
        ":blue-background[**Q × T**] as the primary optimization score.\n\n"
        "QCH imports this real evolution history and lets you explore it through natural-language queries—for example, "
        "comparing circuit versions, examining structural and evaluation metrics, tracing optimization steps, finding "
        "contributor submissions, and studying how circuits evolved over time."
    )
    st.markdown("[Visit the ECDSA.Fail Challenge →](https://ecdsa.fail/)")  # external markdown links open in a new tab

config = load_config()
health = run_health_checks(config)

status_cols = st.columns(3)
llm_tile_label = "Local LLM" if config.llm_enabled else "Natural-language LLM planner"
for col, (label, status) in zip(status_cols, [("QCH Database", health.database), (llm_tile_label, health.llm), ("Query Engine", health.query_engine)]):
    with col:
        if status.disabled:
            st.caption(f"{label}")
            st.info(status.label, icon="⏸️")
        elif status.ok:
            st.caption(f"{label}")
            st.success(f"✓ {status.label}", icon="✅")
        else:
            st.caption(f"{label}")
            st.error(f"✗ {status.label}", icon="❌")

if not health.database.ok:
    st.error(f"QCH database not found or unreadable:\n\n{config.db_path}\n\nPlease restore the Phase 1.7 development snapshot before using Ask QCH.")
    st.stop()

if not health.llm.ok:
    st.warning(f"Local QCH language model is not available.\n\n{health.llm.detail}\n\nStart Ollama and ensure `{config.llm_model}` is installed, then reload this page.")
    # Do not st.stop() here -- the rest of the page still renders; a question asked
    # now will simply surface an honest EXECUTION_ERROR/timeout rather than crash.

with st.sidebar:
    st.subheader("Ask QCH")
    if st.button("New Query / Clear Conversation", width="stretch"):
        _reset_conversation()
        st.rerun()
    st.divider()
    st.caption("Example questions")
    for example in EXAMPLE_QUESTIONS:
        if st.button(example, key=f"example_{example}", width="stretch"):
            _process_input(config, example)
            st.rerun()

# -- conversation history --------------------------------------------------------

for i, message in enumerate(st.session_state.ask_qch_messages):
    with st.chat_message(message["role"]):
        if message["role"] == "user":
            st.write(message["content"])
            continue

        result = message.get("result")
        if result is None:
            st.write(message["content"])
            if message.get("error_detail"):
                with st.expander("Developer details (local only)"):
                    st.code(message["error_detail"])
            continue

        icon, label = status_icon_and_label(result.status)
        if result.status != SystemStatus.ANSWERED:
            st.caption(f"{icon} {label}")
        st.write(result.answer)

        # QCH Phase 2D.6: contributor account card / unresolved-identity notice
        unresolved_notice = format_unresolved_notice(result)
        if unresolved_notice:
            st.info(unresolved_notice)
        contributor_card = format_contributor_summary(result)
        if contributor_card:
            st.table({label: [value] for label, value in contributor_card})

        coverage_line = format_coverage_line(result)
        if coverage_line:
            st.caption(f"\U0001f4ca Data coverage: {coverage_line}")

        if result.status == SystemStatus.NEEDS_CLARIFICATION and result.clarification_options:
            st.caption("Options: " + ", ".join(result.clarification_options))

        plan_sections = format_plan_sections(result)
        raw_sections = format_raw_result_rows(result)
        provenance_fields = format_provenance_fields(result)
        latency_fields = format_latency_fields(result)

        with st.expander("Show Query Plan"):
            if plan_sections["was_repaired"]:
                st.caption("Original planner plan")
                st.json(plan_sections["original"])
                st.caption("Effective executed plan (after SemanticGuard repair)")
                st.json(plan_sections["effective"])
            elif plan_sections["effective"] is not None:
                st.json(plan_sections["effective"])
            else:
                st.write("No query plan was produced for this turn.")

        with st.expander("Show Raw Results"):
            if raw_sections["kind"] == "list":
                st.write(f"{raw_sections['total_count']} row(s){' (showing first ' + str(len(raw_sections['rows'])) + ')' if raw_sections['truncated'] else ''}")
                st.dataframe(raw_sections["rows"])
            elif raw_sections["kind"] == "scalar":
                st.json(raw_sections["scalar"])
            else:
                st.write("No raw results for this turn.")

        with st.expander("Show Provenance"):
            for pf_label, pf_value in provenance_fields:
                st.write(f"**{pf_label}:** {pf_value}")
            for attribution in format_contributor_attribution(result):
                st.write(f"**Contributor attribution:** {attribution}")
            if latency_fields:
                st.caption("Latency")
                for lat_label, lat_value in latency_fields:
                    st.write(f"{lat_label}: {lat_value}")

# -- pending clarification: clickable options -------------------------------------

pending_context = st.session_state.ask_qch_conversation_context
if pending_context is not None and pending_context.is_awaiting_clarification():
    last_result = st.session_state.ask_qch_last_result
    options = clarification_choices(last_result) if last_result is not None else [OTHER_LABEL]
    st.info(pending_context.clarification_question)
    captions = clarification_captions(last_result) if last_result is not None else [""]
    choice = st.radio("Choose one:", options, captions=captions, key=f"clarify_choice_{len(st.session_state.ask_qch_messages)}")
    other_text = None
    if choice == OTHER_LABEL:
        other_text = st.text_input("Type your own clarification:", key=f"clarify_other_{len(st.session_state.ask_qch_messages)}")
    if st.button("Submit clarification"):
        chosen = other_text if choice == OTHER_LABEL else choice
        if chosen:
            _process_input(config, chosen)
            st.rerun()

# -- free-text chat input (also usable to answer a pending clarification) -------

user_typed = st.chat_input("Ask a question about QCH...")
if user_typed:
    _process_input(config, user_typed)
    st.rerun()
