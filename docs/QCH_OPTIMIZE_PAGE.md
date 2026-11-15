# Optimize Your Circuit page

`pages/Optimize_Circuit.py` lets a visitor paste or upload an OpenQASM 2
circuit and returns the best circuit found by QCH's pass-order search. It is
listed in the sidebar as **Optimize Your Circuit** (`src/qch_navigation.py`).

## Flow

1. Input: a text area (pre-filled with a 3-qubit example; **Load example**
   and **Clear** buttons) or an uploaded `.qasm` file.
2. Options:
   * objective (priority order, no weighted score): two-qubit gates → depth
     → total gates (default), depth first, or total gates first;
   * passes: A, B, C, F, G (120 orders, default) or A, B, C (6 orders);
   * search: all orders (default) or greedy.
3. **Optimize** calls `qch_optimizer.optimize.optimize_circuit(...,
   time_limit_s=30, verify="auto")`. The input is translated to rz, sx, x,
   cx; this baseline is a candidate itself, so the result is never worse.
4. Output: the best values with the change against the baseline, the best
   pass order with the Qiskit pass names and the verification status, a
   chart of the metrics after every pass, the best circuit as OpenQASM 2,
   downloads of the circuit and of a JSON report, the ties, the Pareto
   front, excluded results (if any), the pass letters and the platform.
5. If the circuit text changes after a run, the page says that the shown
   result belongs to the previous text.

Nothing is stored: the result is kept only in the Streamlit session.

## Limits (public page)

At most 10 qubits, 3000 operations, 200 kB of text and 30 s of search.
Measurements must be at the end of each qubit; reset, classical control and
parameters are not supported. Results with up to 10 qubits are verified
exactly (same unitary up to global phase and the same measurement mapping).
Invalid or too large input shows an error and no result.

## Optimizer code

`src/qch_optimizer/` contains byte-identical copies of `qch.optimization`,
`qch.objectives` and `qch.optimize` from the `qch-database` repository
(see `docs/OPTIMIZE_API.md` there). They are copied rather than installed
because this repository has its own `qch` package. They match
`qch-database` commit `393a31c` (2026-11-15). `SOURCE.json` records that
commit and the SHA-256 of each file, and a test fails if a copy is edited
here. To
update: change `qch-database`, copy the three files again, update the
hashes and `source_commit`.

The page needs `qiskit` at runtime, so `requirements.txt` now pins
`qiskit==2.5.2`, and `tests/test_ask_qch_deployment.py` requires it there
(the other development-only packages remain excluded).

## Tests and acceptance

- `tests/test_optimize_circuit_page.py` (AppTest, 13 tests): navigation,
  vendored file hashes, no clash with the demo's `qch` package, qiskit in
  the runtime requirements, the example circuit (two-qubit gates 7 → 3,
  exact verification, the best circuit is equivalent to the input and uses
  only rz, sx, x, cx), objective / pass set / search options, errors for
  invalid or too large circuits, the stale-result notice and the buttons.
- Full suite on Linux: 1552 passed, 53 skipped.
- Browser check (2026-11-15, Linux, Streamlit 1.63.0, headless Chromium):
  the example gives 13 / 3 / 9 (gates / two-qubit / depth; baseline
  20 / 7 / 15) with a step chart and two downloads; uploading a 4-qubit
  GHZ file with redundant CX gates replaces the text, shows the stale
  notice, and optimizing gives 6 / 3 / 6; an invalid gate shows a parse
  error; no exception.
- With pass G, total gates and depth can differ slightly between platforms;
  the page shows the platform of the run.
