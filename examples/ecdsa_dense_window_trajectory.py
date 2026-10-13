"""DB-5 Phase A11.7: trajectory analysis (T1-T10) over the dense
25-version window (chain indices 444-468, all real 2026-08-21 promoted
submissions). Every fact here is read from already-persisted QCH data
(hub.metrics/hub.evolution) -- nothing is estimated or interpolated.
Missing data is always reported as missing, never silently skipped or
treated as zero/no-change.

Usage:

    python examples/ecdsa_dense_window_trajectory.py --store <path>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from qch import QCH  # noqa: E402
from qch.history.version_graph import RELATION_NEXT_PROMOTED_COMMIT  # noqa: E402

LOGICAL_CIRCUIT_ID = "qch:logical:secp256k1_point_add"
WINDOW_START, WINDOW_END = 444, 468
TOFFOLI = "structural.toffoli_count"
QUBIT = "structural.qubit_count"
FLAT_TOLERANCE_PCT = 0.1  # T9's explicit tolerance: |pct_change| <= 0.1%


def _dense_chain(hub):
    circuit = hub.circuits.get(LOGICAL_CIRCUIT_ID)
    versions = hub.versions.list(circuit.logical_circuit_id)
    by_id = {v.version_id: v for v in versions}
    has_incoming = set()
    for v in versions:
        for e in hub.evolution.list_edges_from(v.version_id):
            if e.relation_type == RELATION_NEXT_PROMOTED_COMMIT:
                has_incoming.add(e.target_version_id)
    promoted_ids = [
        v.version_id
        for v in versions
        if v.realized_from_submission_id
        and hub.submissions.list_source_commits(hub.submissions.get(v.realized_from_submission_id).submission_id, role="promoted")
    ]
    starts = [vid for vid in promoted_ids if vid not in has_incoming]
    assert len(starts) == 1, f"expected exactly 1 chain start, got {len(starts)}"
    chain = [by_id[starts[0]]]
    current = chain[0]
    while True:
        succs = [e for e in hub.evolution.list_edges_from(current.version_id) if e.relation_type == RELATION_NEXT_PROMOTED_COMMIT]
        if not succs:
            break
        current = by_id[succs[0].target_version_id]
        chain.append(current)
    return chain


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True)
    args = parser.parse_args()

    with QCH.open(args.store) as hub:
        chain = _dense_chain(hub)
        assert len(chain) == 520, f"expected dense chain length 520, got {len(chain)}"
        window = chain[WINDOW_START : WINDOW_END + 1]

        records = []
        for i, v in zip(range(WINDOW_START, WINDOW_END + 1), window):
            toffoli_metric = hub.metrics.get(v.version_id, TOFFOLI)
            qubit_metric = hub.metrics.get(v.version_id, QUBIT)
            records.append(
                {
                    "idx": i,
                    "version_id": v.version_id,
                    "key": v.external_version_key,
                    "time": v.historical_time,
                    "toffoli": toffoli_metric.metric_value if toffoli_metric else None,
                    "qubit": qubit_metric.metric_value if qubit_metric else None,
                }
            )

        # ---- T1 ----
        print("=" * 100)
        print("T1. structural.toffoli_count for all 25 versions, in chain order")
        for r in records:
            val = f"{r['toffoli']:.0f}" if r["toffoli"] is not None else "MISSING DATA"
            print(f"  idx={r['idx']:4d} {r['key']:14s} {r['time']}  toffoli={val}")

        n_with_toffoli = sum(1 for r in records if r["toffoli"] is not None)
        n_with_qubit = sum(1 for r in records if r["qubit"] is not None)
        print(f"\n{n_with_toffoli}/25 versions have {TOFFOLI}; {n_with_qubit}/25 have {QUBIT}.")

        # ---- transitions (pairwise) ----
        transitions = []
        for a, b in zip(records, records[1:]):
            entry = {"from": a, "to": b}
            if a["toffoli"] is not None and b["toffoli"] is not None:
                entry["toffoli_delta"] = b["toffoli"] - a["toffoli"]
                entry["toffoli_pct"] = (entry["toffoli_delta"] / a["toffoli"] * 100.0) if a["toffoli"] != 0 else None
            else:
                entry["toffoli_delta"] = None
                entry["toffoli_pct"] = None
            if a["qubit"] is not None and b["qubit"] is not None:
                entry["qubit_delta"] = b["qubit"] - a["qubit"]
            else:
                entry["qubit_delta"] = None
            transitions.append(entry)

        n_toffoli_covered = sum(1 for t in transitions if t["toffoli_delta"] is not None)
        n_qubit_covered = sum(1 for t in transitions if t["qubit_delta"] is not None)
        print(f"\nOf 24 consecutive transitions: {n_toffoli_covered} have {TOFFOLI} on both endpoints; "
              f"{n_qubit_covered} have {QUBIT} on both endpoints.")

        # ---- T2 ----
        print("\n" + "=" * 100)
        print("T2. Every covered consecutive transition, ranked by absolute Toffoli change (most negative first)")
        covered = [t for t in transitions if t["toffoli_delta"] is not None]
        covered_sorted = sorted(covered, key=lambda t: t["toffoli_delta"])
        for t in covered_sorted:
            pct = f"{t['toffoli_pct']:.4f}%" if t["toffoli_pct"] is not None else "n/a"
            print(f"  {t['from']['key']} -> {t['to']['key']}: delta={t['toffoli_delta']:+.0f} ({pct})")
        missing_transitions = [t for t in transitions if t["toffoli_delta"] is None]
        if missing_transitions:
            print(f"  ({len(missing_transitions)} transition(s) MISSING DATA -- excluded above, not treated as zero change):")
            for t in missing_transitions:
                print(f"    {t['from']['key']} -> {t['to']['key']}: missing on {'both' if t['from']['toffoli'] is None and t['to']['toffoli'] is None else ('from' if t['from']['toffoli'] is None else 'to')} side")

        # ---- T3 ----
        print("\n" + "=" * 100)
        print("T3. Largest one-step improvement and largest one-step regression")
        if covered:
            best = min(covered, key=lambda t: t["toffoli_delta"])
            worst = max(covered, key=lambda t: t["toffoli_delta"])
            print(f"  Largest DECREASE (improvement): {best['from']['key']} -> {best['to']['key']}: {best['toffoli_delta']:+.0f} ({best['toffoli_pct']:.4f}%)")
            print(f"  Largest INCREASE (regression):  {worst['from']['key']} -> {worst['to']['key']}: {worst['toffoli_delta']:+.0f} ({worst['toffoli_pct']:.4f}%)")
        else:
            print("  No covered transitions -- cannot answer (MISSING DATA), not a genuine empty result.")

        # ---- T4: longest consecutive run of decreases ----
        print("\n" + "=" * 100)
        print("T4. Longest consecutive run of Toffoli decreases")
        print("  Definition: a run is a maximal sequence of ADJACENT, fully-covered transitions each with")
        print("  toffoli_delta < 0. A transition with MISSING DATA breaks any run (it is not assumed to continue it).")
        best_run = []
        current_run = []
        for t in transitions:
            if t["toffoli_delta"] is not None and t["toffoli_delta"] < 0:
                current_run.append(t)
                if len(current_run) > len(best_run):
                    best_run = current_run[:]
            else:
                current_run = []
        if best_run:
            print(f"  Longest run: {len(best_run)} consecutive decreasing transition(s), "
                  f"from {best_run[0]['from']['key']} to {best_run[-1]['to']['key']}")
            total = sum(t["toffoli_delta"] for t in best_run)
            print(f"  Total change over that run: {total:+.0f}")
        else:
            print("  No run of decreases found in the currently covered data.")

        # ---- T5: regressions and recovery distance ----
        print("\n" + "=" * 100)
        print("T5. Every regression and how many subsequent covered versions were needed to fall back below the pre-regression level")
        regressions = [(idx, t) for idx, t in enumerate(transitions) if t["toffoli_delta"] is not None and t["toffoli_delta"] > 0]
        if not regressions:
            print("  No regressions found among covered transitions.")
        for idx, t in regressions:
            pre_value = t["from"]["toffoli"]
            post_records = [r for r in records[idx + 2 :] if r["toffoli"] is not None]  # versions strictly after the regression's "to" version
            recovered_after = None
            for hop, r in enumerate(post_records, start=1):
                if r["toffoli"] < pre_value:
                    recovered_after = (hop, r)
                    break
            if recovered_after is None:
                print(f"  Regression at {t['to']['key']} (from {t['from']['key']}, {pre_value:.0f} -> {t['to']['toffoli']:.0f}): "
                      f"NOT YET recovered within currently covered data ({len(post_records)} later covered version(s) checked).")
            else:
                hop, r = recovered_after
                print(f"  Regression at {t['to']['key']} (from {t['from']['key']}, {pre_value:.0f} -> {t['to']['toffoli']:.0f}): "
                      f"recovered after {hop} subsequent covered version(s), at {r['key']} ({r['toffoli']:.0f})")

        # ---- T6: cumulative change, first to last COVERED ----
        print("\n" + "=" * 100)
        print("T6. Cumulative Toffoli change from the first to the last covered version in the window")
        covered_records = [r for r in records if r["toffoli"] is not None]
        if len(covered_records) >= 2:
            first, last = covered_records[0], covered_records[-1]
            delta = last["toffoli"] - first["toffoli"]
            pct = (delta / first["toffoli"] * 100.0) if first["toffoli"] != 0 else None
            note = "" if (first is records[0] and last is records[-1]) else "  (NOTE: window's first/last positions themselves lack data; this spans the first/last COVERED version instead, not idx 444/468 literally)"
            print(f"  {first['key']} ({first['toffoli']:.0f}) -> {last['key']} ({last['toffoli']:.0f}): "
                  f"{delta:+.0f} ({pct:.4f}%){note}")
        else:
            print("  Fewer than 2 covered versions -- cannot compute (MISSING DATA).")

        # ---- T7 ----
        print("\n" + "=" * 100)
        print("T7. Transitions where Toffoli decreases while qubit count increases")
        hits = [t for t in transitions if t["toffoli_delta"] is not None and t["qubit_delta"] is not None and t["toffoli_delta"] < 0 and t["qubit_delta"] > 0]
        if hits:
            for t in hits:
                print(f"  {t['from']['key']} -> {t['to']['key']}: toffoli {t['toffoli_delta']:+.0f}, qubit {t['qubit_delta']:+.0f}")
        else:
            n_both_covered = sum(1 for t in transitions if t["toffoli_delta"] is not None and t["qubit_delta"] is not None)
            print(f"  None found among the {n_both_covered} transition(s) with both metrics covered on both endpoints "
                  f"(genuine empty result, not missing data, provided n_both_covered > 0).")

        # ---- T8 ----
        print("\n" + "=" * 100)
        print("T8. Transitions where BOTH Toffoli count and qubit count decrease")
        hits = [t for t in transitions if t["toffoli_delta"] is not None and t["qubit_delta"] is not None and t["toffoli_delta"] < 0 and t["qubit_delta"] < 0]
        if hits:
            for t in hits:
                print(f"  {t['from']['key']} -> {t['to']['key']}: toffoli {t['toffoli_delta']:+.0f}, qubit {t['qubit_delta']:+.0f}")
        else:
            n_both_covered = sum(1 for t in transitions if t["toffoli_delta"] is not None and t["qubit_delta"] is not None)
            print(f"  None found among the {n_both_covered} transition(s) with both metrics covered on both endpoints.")

        # ---- T9 ----
        print("\n" + "=" * 100)
        print(f"T9. Periods where Toffoli is approximately flat (explicit tolerance: |pct_change| <= {FLAT_TOLERANCE_PCT}%)")
        flat_run = []
        best_flat = []
        for t in transitions:
            if t["toffoli_pct"] is not None and abs(t["toffoli_pct"]) <= FLAT_TOLERANCE_PCT:
                flat_run.append(t)
                if len(flat_run) > len(best_flat):
                    best_flat = flat_run[:]
            else:
                flat_run = []
        if best_flat:
            print(f"  Longest flat run: {len(best_flat)} consecutive transition(s), from {best_flat[0]['from']['key']} to {best_flat[-1]['to']['key']}")
            for t in best_flat:
                print(f"    {t['from']['key']} -> {t['to']['key']}: {t['toffoli_delta']:+.0f} ({t['toffoli_pct']:.4f}%)")
        else:
            print(f"  No transition falls within the {FLAT_TOLERANCE_PCT}% tolerance among covered data.")

        # ---- T10 ----
        print("\n" + "=" * 100)
        print("T10. Factual summary of the 2026-08-21 window (no unqualified 'better/worse' claims)")
        print(f"  - {n_with_toffoli}/25 versions in this window have structural.toffoli_count; "
              f"{n_toffoli_covered}/24 consecutive transitions are fully covered for that metric.")
        if covered:
            print(f"  - structural.toffoli_count ranges from {min(r['toffoli'] for r in covered_records):.0f} to "
                  f"{max(r['toffoli'] for r in covered_records):.0f} among covered versions.")
            print(f"  - The largest single-step DECREASE in structural.toffoli_count was {best['toffoli_delta']:+.0f} "
                  f"({best['from']['key']} -> {best['to']['key']}); the largest single-step INCREASE was "
                  f"{worst['toffoli_delta']:+.0f} ({worst['from']['key']} -> {worst['to']['key']}).")
        print("  - No claim is made about score or benchmark/executed Toffoli count (STORED, V1-V5 only) for any "
              "version in this window -- none of these 25 versions has that metric.")


if __name__ == "__main__":
    main()
