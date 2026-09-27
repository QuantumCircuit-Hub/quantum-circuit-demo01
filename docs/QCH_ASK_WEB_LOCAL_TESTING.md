# Ask QCH -- Local Manual Testing Checklist (Windows)

This is for YOU to follow manually in PowerShell. Everything here runs
entirely on your own machine -- nothing is deployed publicly.

## A. Prerequisites

- Ollama installed and this repo's Python virtual environment already
  set up (`.venv`) -- both already true if you've been following this
  project.
- The real database at `data\qch_ecdsa_p17.sqlite3` (already present
  from Phase 2B).

## B. Start Ollama

Ollama usually runs as a background service already. To check, or to
start it manually:

```powershell
ollama list
```

If that fails to connect, start the server (run this in its own
terminal window and leave it running):

```powershell
ollama serve
```

## C. Verify Qwen2.5-14B is installed

```powershell
ollama list
```

You should see `qwen2.5:14b-instruct-q4_K_M` in the list. If not:

```powershell
ollama pull qwen2.5:14b-instruct-q4_K_M
```

## D. Start Ask QCH

From the project root (`quantum-circuit-hub`):

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

## E. Open it

Streamlit prints a local URL, normally:

```
http://localhost:8501
```

Open that in your browser, then click **"Ask QCH"** in the left
sidebar (it appears alongside the existing "app" and "circuit detail"
pages).

At the top of the page you should see three green checkmarks:

```
QCH Database      ✓ Ready
Local LLM         ✓ Ready
Query Engine      ✓ Ready
```

If "Local LLM" shows a red X, go back to steps B/C.

## F. 10 recommended questions to try

1. **Which version has the lowest structural Toffoli count?** (click the example button)
2. **Which transition reduced structural Toffoli count the most?** (click the example button)
3. **Which versions have structural Toffoli count below 950000?** (click the example button)
4. **Which validated-unpromoted versions branched from ecdsafail:1196b9f?** (click the example button)
5. **Which version is best?** (click the example button)
6. **Which version has the lowest Toffoli count?** (type this one)
7. **What is the structural Toffoli count of ecdsafail:a31a47b?** (type this one)
8. **How many versions did it take to recover after the largest regression?** (type this one)
9. **Which version has the lowest quantum_goodness_score?** (type this one)
10. **Compare version does-not-exist with V5.** (type this one)

## G. What to expect for each

| # | Expect |
|---|---|
| 1 | A direct answer: `ecdsafail:152fbe0`, Toffoli count 988,969, plus a "Data coverage: 25 of 781..." line. |
| 2 | A direct answer showing a **negative** delta (a real reduction, e.g. -12,497). If you expand "Show Query Plan" you may see an "Original planner plan" with `order: desc` and an "Effective executed plan" with `order: asc` -- that's SemanticGuard quietly fixing the model's mistake. |
| 3 | Likely "No matching records were found" plus a coverage line -- this is CORRECT: no real version in this dataset has Toffoli count below 950,000. |
| 4 | A list of ~22 real branched-off version identifiers. |
| 5 | **Does not answer directly.** Instead asks you to clarify (see section H). |
| 6 | **Also asks for clarification** -- "structural" vs. bare Toffoli count are different metrics. |
| 7 | "Data not available" -- this version has no structural Toffoli data (a real, honest gap in the dataset). |
| 8 | An honest "not currently supported" message. |
| 9 | "Unknown metric" -- `quantum_goodness_score` isn't real. |
| 10 | "Unknown version/entity" -- `does-not-exist` isn't a real version. |

## H. How to test clarification

For question 5 or 6 above: after QCH asks its clarification question,
you'll see a list of clickable options (radio buttons) plus an "Other
(type my own)" choice. Click one option, then click **"Submit
clarification"**. QCH will continue your ORIGINAL question -- you never
have to retype it.

## I. How to inspect Query Plan / Coverage / Provenance

Under every answer there are three collapsible sections:

- **Show Query Plan** -- the exact plan that was executed (and, if
  SemanticGuard corrected something, the original vs. corrected plan
  side by side).
- **Show Raw Results** -- the actual rows/values returned by the
  database (capped at 20 rows if there are more, with the real total
  count shown).
- **Show Provenance** -- every internal status (planner, SemanticGuard,
  ResultSanityChecker), which metrics/relations were used, and a
  latency breakdown.

The coverage line ("Data coverage: ...") appears directly under the
answer, not hidden in an expander.

## J. How to clear the conversation

Click **"New Query / Clear Conversation"** in the left sidebar. This
wipes the visible conversation and any pending clarification -- it does
NOT modify the database.

## K. How to stop the application

Go back to the PowerShell window running Streamlit and press
`Ctrl+C`. If you started `ollama serve` manually in its own window,
`Ctrl+C` there too (if Ollama runs as a background Windows service
instead, you can leave it running).

## L. Phase 2D.1 -- arbitrary version lookups and canonical IDs

As of Phase 2D.1, answers show a version's real canonical ID
(`ecdsafail:<7-hex>`) as the PRIMARY identity, with a milestone label
(only V1-V5 have one) shown alongside it, never instead of it. You can
also now ask about any of the 781 real versions, not just V1-V5, by
its short or full form:

1. **Which version has the lowest score?** -- now answers with
   `ecdsafail:a39e07e (milestone V5)`, not bare "V5".
2. **What is the structural Toffoli count of ecdsafail:c55911f?** --
   a real, non-milestone version, addressed by its full canonical key.
3. **What is the structural Toffoli count of c55911f?** -- the SAME
   real version, addressed by its bare short ID (no `ecdsafail:`
   prefix). May or may not be recognized depending on how the model
   phrases the plan; see the Phase 2D.1 completion report for what was
   actually observed with the real model.
4. **What is the structural Toffoli count of b6f2b0a?** -- expect
   "unknown entity" (this identifier does not exist in the real
   database, verified directly).
5. **Compare ecdsafail:c55911f and V5.** -- a real arbitrary version
   compared against a real milestone.

## M. Common troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| "QCH Database" shows a red X | `data\qch_ecdsa_p17.sqlite3` is missing. Restore the Phase 1.7 snapshot into that exact path. |
| "Local LLM" shows a red X | Ollama isn't running, or the model isn't pulled. Run `ollama list`; if empty/errors, run `ollama serve` in its own window, then `ollama pull qwen2.5:14b-instruct-q4_K_M`. |
| A question takes 20-90+ seconds | Normal -- this is real local LLM inference on a 14B model, not a bug. Larger/compositional questions take longer. |
| "Something went wrong while processing this question" | Expand "Developer details (local only)" under that message for the exact error. This should be rare; if you see it, it's worth reporting. |
| The page looks stuck on "Thinking..." | Wait -- some questions genuinely take over a minute. If it never finishes (multiple minutes), check the PowerShell window running Streamlit for errors, and confirm Ollama is still responsive with `ollama list`. |
