"""
Mitigation scenario modeling.

A "scenario" is a set of mitigation IDs that we hypothetically implement. For
each scenario, we re-run the Monte Carlo with the mitigations applied to their
target risks (reducing each risk's firing probability) and report the
before/after deltas on:

    * P50, P80, P95 final cost
    * P50, P80, P95 LRR slip in weeks
    * Total mitigation cost
    * Risk reduction achieved
    * ROI = (P80 cost reduction) / mitigation cost

Outputs:
    * mitigation_catalog()       -> proposed/approved/in-progress mitigations
    * evaluate_scenario(mit_ids) -> single-row delta DataFrame
    * compare_scenarios(...)     -> multi-row DataFrame for executive view
    * print_before_after()       -> the resume-bullet story

Run:
    python -m src.analytics.mitigation
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from src.config import RANDOM_SEED, fmt_usd
from src.db import get_conn
from src.analytics.monte_carlo import (
    simulate_cost,
    simulate_schedule,
    N_ITERATIONS_DEFAULT,
)


# ----------------------------------------------------------------------------
# Data load
# ----------------------------------------------------------------------------
def mitigation_catalog() -> pd.DataFrame:
    """All actionable mitigations (Proposed, Approved, or In Progress)."""
    sql = """
        SELECT
            m.mitigation_id,
            m.risk_id,
            r.title           AS risk_title,
            r.workstream_id,
            m.description,
            m.cost_usd,
            m.expected_risk_reduction_pct AS reduction_pct,
            m.status,
            r.probability     AS risk_probability,
            r.impact_cost_usd AS risk_impact_cost,
            r.impact_schedule_days AS risk_impact_days,
            r.exposure_usd    AS risk_exposure
        FROM mitigation_actions m
        JOIN risk_register r ON r.risk_id = m.risk_id
        WHERE m.status IN ('Proposed', 'Approved', 'In Progress')
        ORDER BY (r.probability * r.impact_cost_usd * m.expected_risk_reduction_pct)
                 DESC
    """
    with get_conn() as conn:
        df = pd.read_sql_query(sql, conn)
    df["expected_risk_reduction_usd"] = df["risk_exposure"] * df["reduction_pct"]
    df["roi_expected"] = df["expected_risk_reduction_usd"] / df["cost_usd"]
    return df


def _mitigation_map(mitigation_ids: Iterable[str]) -> dict:
    """
    Convert a list of mitigation IDs into the {risk_id: combined_reduction_pct}
    dict that simulate_cost / simulate_schedule expects.

    If two mitigations target the same risk, we combine them multiplicatively
    so that two 50% mitigations leave 0.5 * 0.5 = 25% of the residual risk
    (i.e. a combined 75% reduction). This is the standard convention.
    """
    cat = mitigation_catalog().set_index("mitigation_id")
    combined: dict[str, float] = {}
    for mid in mitigation_ids:
        if mid not in cat.index:
            continue
        risk_id = cat.loc[mid, "risk_id"]
        reduction = float(cat.loc[mid, "reduction_pct"])
        residual_prior = 1 - combined.get(risk_id, 0.0)
        residual_after = residual_prior * (1 - reduction)
        combined[risk_id] = 1 - residual_after
    return combined


# ----------------------------------------------------------------------------
# Scenario evaluation
# ----------------------------------------------------------------------------
def _summarize(arr: np.ndarray) -> dict[str, float]:
    return {
        "mean": float(arr.mean()),
        "p50":  float(np.percentile(arr, 50)),
        "p80":  float(np.percentile(arr, 80)),
        "p95":  float(np.percentile(arr, 95)),
    }


def evaluate_scenario(
    mitigation_ids: Iterable[str],
    n: int = N_ITERATIONS_DEFAULT,
    scenario_name: str = "Scenario",
) -> pd.DataFrame:
    """
    Run the cost + schedule Monte Carlo with and without the given
    mitigations and return a single-row delta summary.

    NOTE: we re-use the same RNG seed for both runs so that the random
    *background* (performance uncertainty draws, other risk firings) is held
    constant. This isolates the effect of the mitigations themselves and is
    the standard variance-reduction technique for paired Monte Carlo
    comparisons.
    """
    mit_map = _mitigation_map(mitigation_ids)

    # Baseline (no mitigations).
    cost_before  = simulate_cost(n=n, rng_seed=RANDOM_SEED)
    sched_before = simulate_schedule(n=n, rng_seed=RANDOM_SEED + 1)
    # Mitigated.
    cost_after   = simulate_cost(n=n, rng_seed=RANDOM_SEED, mitigations=mit_map)
    sched_after  = simulate_schedule(n=n, rng_seed=RANDOM_SEED + 1, mitigations=mit_map)

    cb, ca = _summarize(cost_before), _summarize(cost_after)
    sb, sa = _summarize(sched_before), _summarize(sched_after)

    cat = mitigation_catalog().set_index("mitigation_id")
    used = [mid for mid in mitigation_ids if mid in cat.index]
    total_mit_cost = float(cat.loc[used, "cost_usd"].sum())

    # ROI based on P80 cost reduction (the planning-reserve metric).
    p80_cost_reduction = cb["p80"] - ca["p80"]
    roi_p80 = p80_cost_reduction / total_mit_cost if total_mit_cost else 0.0

    return pd.DataFrame([{
        "scenario":                scenario_name,
        "mitigations":             ",".join(used),
        "n_mitigations":           len(used),
        "mitigation_cost_usd":     total_mit_cost,

        "cost_p50_before":         cb["p50"],
        "cost_p50_after":          ca["p50"],
        "cost_p50_reduction":      cb["p50"] - ca["p50"],

        "cost_p80_before":         cb["p80"],
        "cost_p80_after":          ca["p80"],
        "cost_p80_reduction":      cb["p80"] - ca["p80"],

        "cost_p95_before":         cb["p95"],
        "cost_p95_after":          ca["p95"],
        "cost_p95_reduction":      cb["p95"] - ca["p95"],

        "sched_p50_weeks_before":  sb["p50"] / 7,
        "sched_p50_weeks_after":   sa["p50"] / 7,
        "sched_p50_weeks_recov":   (sb["p50"] - sa["p50"]) / 7,

        "sched_p80_weeks_before":  sb["p80"] / 7,
        "sched_p80_weeks_after":   sa["p80"] / 7,
        "sched_p80_weeks_recov":   (sb["p80"] - sa["p80"]) / 7,

        "roi_p80_cost":            roi_p80,
    }])


def compare_scenarios(
    scenarios: dict[str, list[str]],
    n: int = N_ITERATIONS_DEFAULT,
) -> pd.DataFrame:
    """Run multiple scenarios and stack the results."""
    return pd.concat(
        [evaluate_scenario(ids, n=n, scenario_name=name)
         for name, ids in scenarios.items()],
        ignore_index=True,
    )


# ----------------------------------------------------------------------------
# Executive printout -- the BEFORE vs. AFTER story
# ----------------------------------------------------------------------------
def print_executive_summary(n: int = N_ITERATIONS_DEFAULT) -> None:
    cat = mitigation_catalog()

    print("=" * 78)
    print(f"  MITIGATION SCENARIOS  --  {n:,} iterations, seed={RANDOM_SEED}")
    print("=" * 78)

    print("  MITIGATION CATALOG  (sorted by expected $ risk reduction)")
    print("-" * 78)
    print(f"  {'ID':<7} {'Risk':<6} {'Cost':>8} {'Red%':>6} "
          f"{'E[Δ$]':>10} {'ROI':>6}  Description")
    for _, r in cat.iterrows():
        desc = r["description"][:34]
        print(
            f"  {r['mitigation_id']:<7} {r['risk_id']:<6} "
            f"{fmt_usd(r['cost_usd']):>8} {r['reduction_pct']*100:>5.0f}% "
            f"{fmt_usd(r['expected_risk_reduction_usd']):>10} "
            f"{r['roi_expected']:>5.1f}x  {desc}"
        )
    print()

    # Three scenarios designed to tell a clear story:
    #   1. "Quick wins" -- the three highest-ROI mitigations
    #   2. "Targeted top-3 risks" -- mitigations for R001/R002/R005
    #   3. "Full program" -- all mitigations
    scenarios = {
        "Quick Wins (top-3 ROI)":   cat.sort_values("roi_expected", ascending=False)
                                      .head(3)["mitigation_id"].tolist(),
        "Top-3 Risks Addressed":    ["MIT001", "MIT002", "MIT005"],
        "Full Mitigation Plan":     cat["mitigation_id"].tolist(),
    }
    comp = compare_scenarios(scenarios, n=n)

    print("-" * 78)
    print("  SCENARIO COMPARISON")
    print("-" * 78)
    print(f"  {'Scenario':<26} {'Cost':>8} {'P80 Δ$':>9} "
          f"{'P80 Δwks':>9} {'ROI':>6}")
    for _, r in comp.iterrows():
        print(
            f"  {r['scenario']:<26} "
            f"{fmt_usd(r['mitigation_cost_usd']):>8} "
            f"{fmt_usd(r['cost_p80_reduction']):>9} "
            f"{r['sched_p80_weeks_recov']:>8.1f}w "
            f"{r['roi_p80_cost']:>5.1f}x"
        )
    print()

    # The resume-bullet "before / after" story uses the full plan.
    full = comp.iloc[-1]
    print("-" * 78)
    print("  BEFORE vs. AFTER  (Full Mitigation Plan)")
    print("-" * 78)
    print(f"  {'Metric':<32} {'Before':>14} {'After':>14} {'Δ':>13}")
    print(f"  {'P50 cost':<32} "
          f"{fmt_usd(full['cost_p50_before']):>14} "
          f"{fmt_usd(full['cost_p50_after']):>14} "
          f"{fmt_usd(-full['cost_p50_reduction']):>13}")
    print(f"  {'P80 cost (planning reserve)':<32} "
          f"{fmt_usd(full['cost_p80_before']):>14} "
          f"{fmt_usd(full['cost_p80_after']):>14} "
          f"{fmt_usd(-full['cost_p80_reduction']):>13}")
    print(f"  {'P95 cost (conservative)':<32} "
          f"{fmt_usd(full['cost_p95_before']):>14} "
          f"{fmt_usd(full['cost_p95_after']):>14} "
          f"{fmt_usd(-full['cost_p95_reduction']):>13}")
    print(f"  {'P80 LRR slip (weeks)':<32} "
          f"{full['sched_p80_weeks_before']:>13.1f}w "
          f"{full['sched_p80_weeks_after']:>13.1f}w "
          f"{-full['sched_p80_weeks_recov']:>12.1f}w")
    print(f"  {'Mitigation investment':<32} "
          f"{'':>14} {fmt_usd(full['mitigation_cost_usd']):>14}")
    print(f"  {'ROI (P80 $ reduction / cost)':<32} "
          f"{'':>14} {full['roi_p80_cost']:>13.1f}x")
    print("=" * 78)


# ----------------------------------------------------------------------------
# Demo
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    print_executive_summary()