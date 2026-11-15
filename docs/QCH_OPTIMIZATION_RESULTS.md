# Optimization Results page

`pages/Optimization_Results.py` compares saved pass-order experiments from
the sibling `qch-database` repository on 12 MQT Bench circuits (all from the
`nativegates` layer, 2 to 6 qubits):

| Pass set | Experiment | Passes | Orders per circuit |
|---|---|---|---|
| Stage 2D | `stage2d_20261114165225` | A, B, C | 6 |
| Stage 2G | `stage2g_20261115052449` | A, B, C, F, G | 120 |

A = CommutativeCancellation, B = Optimize1qGatesDecomposition,
C = CommutativeInverseCancellation, F = Optimize1qGatesSimpleCommutation,
G = TwoQubitBlockResynthesis (a QCH step running Collect2qBlocks,
ConsolidateBlocks and UnitarySynthesis). The target gate set is rz, sx, x, cx.
Both experiments were recorded on Windows AMD64, CPython 3.11.8,
Qiskit 2.5.2.

The page is read-only. Its only data are the two JSON datasets in
`data/optimization/` (`stage2d_abc.json`, `stage2g_abcfg.json`); it does not
open SQLite, QPY snapshots or Qiskit. It is listed in the sidebar as
**Optimization Results** (`src/qch_navigation.py`).

## What the page shows

- **Circuit tab** (default: A, B, C, F, G, source circuit 291):
  - circuit and pass-set selectors; the pass sets are labelled by their
    passes and number of orders, without stage names;
  - a table that always shows what each pass letter means (Qiskit pass and
    a one-line description), without a click;
  - the input baseline and the best value of each metric separately over
    the verified orders, with the best values of the other pass set for
    comparison;
  - whether one order is best in all three metrics at once;
  - the orders for an objective (Pareto front, fewest total gates, fewest
    two-qubit gates, lowest depth, all orders), grouped by identical final
    metrics by default; every order keeps its run ID. There is no combined
    score;
  - for a selected run, a chart and a table of the metrics after every pass.
- **All circuits tab**: baseline and best values for both pass sets for all
  12 circuits, and the change from A, B, C to A, B, C, F, G.
- Runs that failed or were not verified are listed separately and are never
  used for best values or the Pareto front. If a dataset is missing or
  invalid, the page shows a warning and no results.

## Metric profile

All numbers use the experiment metric profile of
`qch-database/experiments/mqt_pass_order.py` (`experiment_metrics`):

- total gates: single-, two- and multi-qubit gates; measure, reset and
  barrier are not counted;
- two-qubit gates: gates acting on exactly two qubits;
- depth: depth after removing final measurements and barriers.

## Datasets

Produced by `experiments/export_demo_dataset.py` in `qch-database`
(PowerShell, from the `qch-database` repository root):

```powershell
$Py   = ".\.venv\Scripts\python.exe"
$Data = "..\Demo01\quantum-circuit-hub\data\optimization"
& $Py experiments\export_demo_dataset.py --out-dir .\demo\stage2d_20261114165225 --output $Data\stage2d_abc.json
& $Py experiments\export_demo_dataset.py --out-dir .\demo\stage2g_20261115052449 --output $Data\stage2g_abcfg.json
```

The script opens `experiment.db` read-only, compares its SHA-256 before and
after, restores every step circuit from its stored QPY snapshot and
recomputes the three metrics. It runs no pass and does not touch the
original MQT database. It refuses to write if a run's first step differs
from the baseline, if the last step differs from the recorded final
metrics, if the runs of a circuit do not share one input representation,
or if a label is not an order of the pass set. The output contains no local
paths.

Format (`schema_version` 2; metric triples are `[gates, two-qubit gates,
depth]`): `metric_profile`, `experiment`, `environment`,
`experiment_db_sha256`, `passes` (letter to Qiskit pass and parameters) and
`circuits`, each with `source_id`, `name`, `family`, `layer`, `num_qubits`,
`input_sha256`, `baseline` and `runs` (`order`, `run_id`, `status`,
`verified`, `final`, `steps`). The page checks all of this, including that
every circuit has one run per order, that run IDs are unique, that step 0
equals the baseline and that the last step equals the final metrics.

### Export record (2026-11-15)

- `stage2d_abc.json`: 12 circuits, 72 runs, 13,788 bytes; source
  `experiment.db` SHA-256 `6238d916f7b4f59a4e3c6f4bdc6043bfbcbc3a0ed50818147d15871ede3d2282`,
  unchanged.
- `stage2g_abcfg.json`: 12 circuits, 1,440 runs, 251,188 bytes; source
  `experiment.db` SHA-256 `4873f3ee0904f04f0a70e1a40913aca5fb51ffcf0aebf3a9f96734fd1e0ca6bd`,
  unchanged.
- Exported on Linux x86_64 (CPython 3.11.17, Qiskit 2.5.2) from
  byte-identical copies of the Windows experiment directories. All runs
  succeeded and are verified.

### Main results shown (best values, gates / two-qubit / depth)

| Source | A, B, C | A, B, C, F, G |
|---|---|---|
| 234 graphstate | 42 / 12 / 14 | 46 / 6 / 16 |
| 274 grover-v-chain | 73 / 18 / 57 | 50 / 9 / 32 |
| 291 portfoliovqe | 233 / 90 / 78 | 206 / 45 / 63 |
| 443 qftentangled | 266 / 70 / 110 | 152 / 35 / 54 |
| 936 vqe | 50 / 8 / 28 | 50 / 4 / 27 |
| 168 ghz | 38 / 10 / 24 | 50 / 5 / 26 |

Adding F and G halves the two-qubit gates on these 6 circuits; on 234 and
168 this costs extra gates or depth. With G, total gates and depth can
differ slightly between platforms; two-qubit counts are reproducible (see
`qch-database/docs/PROGRESS.md`, stages 2G and 2H).

## Tests and acceptance

- `tests/test_optimization_results_page.py` (AppTest, 34 tests):
  navigation and existing routes; the page reads only its JSON datasets;
  both datasets cover the same 12 verified circuits; Pareto front, ties,
  objectives and exclusion of failed or unverified runs; the overview of
  both pass sets; the default view (Stage 2G, source 291); the Stage 2D
  trade-off; saved steps for runs; selector synchronisation when the
  circuit, objective or pass set changes; rejection of malformed datasets;
  and the warning-only fallback for missing or invalid datasets (run from a
  temporary copy); the pass letters are shown without an expander, and no
  stage name appears in the rendered page.
- Full suite on Linux: 1539 passed, 53 skipped.
- Browser check (2026-11-15, Linux, Streamlit 1.63.0, headless Chromium):
  default view 206 / 45 / 63 for source 291 with the "no common optimum"
  note; fewest total gates shows 10 orders at 206 / 45 / 65; lowest depth
  shows 5 orders at depth 63; Stage 2D shows 233 / 90 / 78 and the orders
  A → B → C, A → C → B, C → A → B at 238 / 90 / 78; the step chart is drawn;
  the overview lists 12 circuits; no exception.
- Windows: Stage 3A (the earlier single-circuit version) passed 25 page
  tests. Windows collected 69 tests fewer than Linux before `zstandard`
  was installed (three modules use `pytest.importorskip("zstandard")`).
