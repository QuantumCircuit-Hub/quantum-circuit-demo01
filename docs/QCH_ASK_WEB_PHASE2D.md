# QCH Phase 2D: Ask QCH -- Local Web Interface

## 1. Purpose

A local, chat-style browser interface over the existing, frozen QCH
natural-language pipeline (Phase 2A-2C1). Local-only: no public
deployment, no new backend semantics, no new model. The page is a
presentation layer, not a second implementation of the pipeline.

## 2. Architecture

```
Streamlit page (pages/Ask_QCH.py)
    |
    | collects input, maintains st.session_state, calls the service
    v
qch.nl.service.QCHNaturalLanguageService.ask(question, conversation_context)
    |
    v
NLQueryPlanner -> validator -> SemanticGuard -> QCHQueryExecutor
    -> real qch_ecdsa_p17.sqlite3 -> ResultSanityChecker -> ServiceResult
```

Supporting modules (both new, both `streamlit`-free and independently
unit-tested):

- `src/ask_qch_config.py` -- env-var configuration (`AskQCHConfig`) and
  read-only local health checks (`run_health_checks`).
- `src/ask_qch_presentation.py` -- pure formatting helpers
  (`status_icon_and_label`, `format_coverage_line`,
  `clarification_choices`, `format_plan_sections`,
  `format_raw_result_rows`, `format_provenance_fields`,
  `format_latency_fields`) that turn a `ServiceResult` into plain data
  for the page to render. No query semantics live here or in the page
  itself.

## 3. Relationship to `QCHNaturalLanguageService`

The page never duplicates orchestration. Every question -- typed,
clicked as an example, or submitted as a clarification answer -- goes
through the SAME function, `_process_input()`, which calls
`service.ask(text, conversation_context=...)` exactly once and records
whatever `ServiceResult` comes back. The page reads `result.status`,
`result.answer`, `result.explanation`, `result.raw_result`, and
`result.conversation_context` -- it never re-derives an answer, a
status, or a plan.

**One real, minimal, additive backend change was needed** (Phase 2D
spec section 2's carve-out for "a genuine UI integration bug"):
`ServiceResult` gained a `raw_result: dict | None` field (the real
`QCHQueryResult.to_dict()`, already computed by the service, simply
exposed for the "Show Raw Results" panel). `QCHQueryPlan`,
`QCHQueryExecutor`, query operators, the validator, `SemanticGuard`,
`ResultSanityChecker`, metric semantics, and relation semantics were
NOT touched.

## 4. Session-state model

```python
st.session_state.ask_qch_messages            # [{"role", "content", "result": ServiceResult|None}, ...]
st.session_state.ask_qch_conversation_context # ConversationContext | None (only set while awaiting a clarification)
st.session_state.ask_qch_last_result          # the most recent ServiceResult (for clarification option lookup)
```

Nothing is persisted to the QCH database or to disk -- a browser
refresh resets the conversation (per spec section 20, this is
explicitly acceptable for this first version).

## 5. Clarification flow

When `result.status == NEEDS_CLARIFICATION`, the page renders
`result.clarification_options` (already schema-derived and naturalized
by `SemanticGuard`/`derive_clarification_options`, unchanged) as a
clickable `st.radio`, plus an "Other (type my own)" escape hatch. On
submit, the chosen text is passed straight to `_process_input()` with
the SAME `conversation_context` -- `QCHNaturalLanguageService` itself
reconstructs the merged question (`_build_clarified_question`); the UI
never assembles a query string. Free-text answers typed into the
normal chat box work identically, since both paths call the same
function.

## 6. Answer rendering

`result.answer` is displayed verbatim as the primary content -- the
page never recomputes or rewords it. A small status caption
(icon + label from `STATUS_DISPLAY`) is shown for every non-`ANSWERED`
status so the user always sees *why* rather than a generic failure.

## 7. Coverage display

`format_coverage_line()` reads `result.explanation.coverage.sentence()`
-- the exact, already-computed `CoverageSummary` from the real current
database (e.g. "25 of 781 version(s) currently have
'structural.toffoli_count' recorded."). Never hard-coded; verified
against three different real coverage counts during manual testing
(25/781 for `structural.toffoli_count`, 5/781 for the bare
`toffoli_count`).

## 8. Provenance display

The "Show Provenance" expander lists every field from
`QueryExplanation.to_dict()` that is present (original question,
execution status, SemanticGuard outcome + reason codes, repair actions
with before/after values, ResultSanityChecker outcome, metrics/
relations used, data source), plus the full `LatencyBreakdown`. No
chain-of-thought is ever available to show -- every field is already a
plain fact or machine-readable reason code (see
`docs/QCH_NL_SEMANTIC_RELIABILITY_PHASE2C.md`).

## 9. Error/status handling

All 10 `SystemStatus` values map to a distinct icon/label
(`ask_qch_presentation.STATUS_DISPLAY`); none collapse into "Sorry, I
couldn't answer." An unexpected exception from `service.ask()` itself
(never expected in normal operation, but not assumed impossible) is
caught in `_process_input()`, shown as a generic message, with the raw
exception available in a "Developer details (local only)" expander --
never a raw Streamlit traceback shown to a normal user, and the app
never crashes.

## 10. Configuration

Environment variables, all with local-development defaults (see
`src/ask_qch_config.py`):

| Variable | Default |
|---|---|
| `QCH_DB_PATH` | `data/qch_ecdsa_p17.sqlite3` (relative to repo root) |
| `QCH_LLM_MODEL` | `qwen2.5:14b-instruct-q4_K_M` |
| `QCH_LLM_BASE_URL` | `http://localhost:11434/v1/chat/completions` |
| `QCH_LLM_TIMEOUT_SECONDS` | `400` |

No secrets, no machine-specific absolute paths in code.

## 11. Local startup

```powershell
streamlit run app.py
```
from the project root, then open "Ask QCH" in the sidebar (Streamlit's
classic `pages/` auto-discovery already lists every script under
`pages/`, so no navigation code was added or changed). See
`docs/QCH_ASK_WEB_LOCAL_TESTING.md` for the full manual checklist.

## 12. Testing

- `tests/test_ask_qch_presentation.py` (28 tests): every `SystemStatus`
  rendering, coverage formatting, clarification choices, plan-section
  (SAFE_REPAIR before/after) formatting, raw-result row capping,
  provenance fields, latency fields -- all against mocked
  `ServiceResult`/`QueryExplanation` objects, no Streamlit, no LLM.
- `tests/test_ask_qch_config.py` (8 tests): config defaults/overrides,
  database health-check success/failure, LLM-unreachable detection,
  query-engine health check. One test uses the real project database
  if present (skipped otherwise).
- Real local acceptance: 10 user journeys driven through the ACTUAL
  running Streamlit page in a real browser, the real
  `qwen2.5:14b-instruct-q4_K_M` model, and the real
  `data/qch_ecdsa_p17.sqlite3` database (see the Phase 2D completion
  report for the full journey-by-journey results).

## 13. Limitations

- **A real threading bug was found and fixed during this phase's own
  local testing**: caching the `QCH` (SQLite) hub via
  `st.cache_resource` produced a reproducible
  `sqlite3.ProgrammingError` ("...created in a thread can only be used
  in that same thread...") because Streamlit's ScriptRunner does not
  guarantee a session's reruns execute on the same OS thread. Fixed by
  opening and closing a fresh hub connection WITHIN each single
  `_process_input()` call (see `pages/Ask_QCH.py`'s own comment) rather
  than caching it -- correctness over a negligible amount of local
  performance, and no frozen backend file was touched.
- Real LLM output is not perfectly deterministic even at
  `temperature=0` on this runtime: the SAME identifier
  (`ecdsafail:1196b9f`) was sometimes flagged `UNKNOWN_ENTITY` and
  sometimes handled correctly across different manual test runs with
  slightly different question phrasing -- an existing, already-
  documented Phase 2A-2/2C model-behavior limitation, not a Phase 2D
  UI bug (see the completion report's Journey 5 notes).
- Conversation state is in-memory only (`st.session_state`); a browser
  refresh resets it, per spec section 20's own explicit allowance.
- "Show Raw Results" caps tabular output at 20 rows by default (real
  total count is always shown alongside).
- No answer streaming -- the full LLM planning latency (median ~20-40s,
  occasionally over a minute) is a single blocking spinner. Acceptable
  for a local single-user development tool; a future iteration could
  stream planning progress.

## 14. Future deployment considerations (NOT designed here)

Deliberately out of scope for Phase 2D: authentication, multi-user
session isolation, a hosted Ollama endpoint, secrets management for a
non-local LLM provider, rate limiting, and public network exposure.
Phase 2D ends at "works reliably on localhost."
