"""
Monte Carlo cost and schedule forecasting.

Why two sources of uncertainty
------------------------------
Every aerospace program faces two distinct kinds of cost/schedule uncertainty,
and a defensible forecast must model both:

    1. Performance uncertainty -- even with no new risks, the remaining work
       will not execute exactly at the current CPI. We sample remaining work
       cost from a triangular distribution anchored on observed CPI per
       workstream. (Standard PMO technique; NASA CEH §4, PMBOK §11.4.)

    2. Discrete risk events -- each entry in the risk register fires with its
       declared probability; if it fires, its impact_cost_usd hits the
       program. Same logic with impact_schedule_days for the schedule sim.

Per iteration we sum both contributions; over N=10,000 iterations we report
P50, P80, and P95 outcomes. This is exactly how Lockheed, Boeing, and the
NASA cost-and-schedule offices structure their independent EACs.

Outputs:
    * simulate_cost(n)            -> ndarray of N final-cost outcomes
    * simulate_schedule(n)        -> ndarray of N LRR-slip outcomes (days)
    * cost_distribution_summary() -> P50/P80/P95 + probability of overrun
    * schedule_distribution_summary()
    * risk_contribution_table()   -> per-risk expected contribution

Run:
    python -m src.analytics.monte_carlo
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.config import (
    PROGRAM_BASELINE_USD,
    RANDOM_SEED,
    fmt_usd,
)
from src.db import get_conn
from src.analytics.evm import workstream_summary
from src.analytics.schedule import launch_readiness_forecast


N_ITERATIONS_DEFAULT = 10_000
PERFORMANCE_RANGE = 0.10  # +/-10% triangular spread around observed CPI

# Schedule-side parameters. PMOs typically forecast a small additional
# schedule drift on remaining work even with no new risks (execution slop).
# We model this as a triangular(-3, 0, +7) days per workstream.
SCHEDULE_DRIFT_MIN  = -3
SCHEDULE_DRIFT_MODE =  0
SCHEDULE_DRIFT_MAX  =  7


# ----------------------------------------------------------------------------
# Data loaders
# ----------------------------------------------------------------------------
def _load_open_risks() -> pd.DataFrame:
    sql = """
        SELECT
            risk_id, title, workstream_id,
            probability, impact_cost_usd, impact_schedule_days
        FROM risk_register
        WHERE status IN ('Open', 'Mitigating')
    """
    with get_conn() as conn:
        return pd.read_sql_query(sql, conn)


def _remaining_work_by_workstream() -> pd.DataFrame:
    """
    For each workstream: BAC, work earned to date (EV), observed CPI.
    Remaining work to forecast = BAC - EV.
    """
    ws = workstream_summary()
    out = ws[["workstream_id", "workstream", "bac", "ev", "cpi"]].copy()
    out["remaining_bac"] = (out["bac"] - out["ev"]).clip(lower=0)
    # Use 1.0 as a fallback CPI for workstreams with zero actuals.
    out["cpi"] = out["cpi"].fillna(1.0).clip(lower=0.5, upper=1.5)
    return out


# ----------------------------------------------------------------------------
# Cost simulation
# ----------------------------------------------------------------------------
def simulate_cost(
    n: int = N_ITERATIONS_DEFAULT,
    rng_seed: int = RANDOM_SEED,
    mitigations: dict | None = None,
) -> np.ndarray:
    """
    Simulate N possible program final costs.

    Approach (PMO-standard independent EAC):
        1. Anchor on the deterministic CPI-based EAC from evm.workstream_summary.
           This already reflects current performance trends.
        2. Add performance uncertainty on remaining work via a triangular
           multiplier on remaining_BAC: best case finishes 10% under remaining
           plan, worst case 10% over. (Symmetric because the CPI anchor
           already biases the central tendency the right way.)
        3. Add discrete risk events from the risk register.

    final_cost = AC_to_date
               + sum_ws( remaining_BAC_ws / cpi_ws * uncertainty_multiplier )
               + sum_risk( triggered ? impact : 0 )

    mitigations: optional dict {risk_id: reduction_pct} that reduces that
    risk's effective firing probability for this run.
    """
    rng = np.random.default_rng(rng_seed)

    rem = _remaining_work_by_workstream()
    risks = _load_open_risks()
    mitigations = mitigations or {}

    with get_conn() as conn:
        ac_to_date = conn.execute(
            "SELECT COALESCE(SUM(actual_cost_usd), 0) FROM actual_spend"
        ).fetchone()[0]

    n_ws = len(rem)
    cpi_obs = rem["cpi"].to_numpy()
    remaining_bac = rem["remaining_bac"].to_numpy()

    # Deterministic remaining-cost anchor per workstream: rem_BAC / CPI.
    deterministic_remaining = remaining_bac / cpi_obs

    # Performance uncertainty on remaining work: triangular(1-r, 1, 1+r).
    # PERFORMANCE_RANGE = 0.10 -> remaining cost lands in +/-10% of anchor.
    uncertainty_mult = rng.triangular(
        left=1 - PERFORMANCE_RANGE, mode=1.0, right=1 + PERFORMANCE_RANGE,
        size=(n, n_ws),
    )
    remaining_cost_per_iter = (deterministic_remaining * uncertainty_mult).sum(axis=1)

    # Discrete risk events.
    risk_cost_per_iter = np.zeros(n)
    for _, r in risks.iterrows():
        red = mitigations.get(r["risk_id"], 0.0)
        # Mitigation reduces *expected value*, modeled as a haircut on
        # probability rather than impact. PMO convention: if a mitigation
        # works, the risk doesn't fire.
        adj_prob = r["probability"] * (1 - red)
        fires = rng.random(n) < adj_prob
        risk_cost_per_iter += fires * r["impact_cost_usd"]

    return ac_to_date + remaining_cost_per_iter + risk_cost_per_iter


def cost_distribution_summary(
    n: int = N_ITERATIONS_DEFAULT,
    mitigations: dict | None = None,
) -> pd.DataFrame:
    """P50, P80, P95 and probability of breaching the baseline."""
    sims = simulate_cost(n=n, mitigations=mitigations)
    return pd.DataFrame([{
        "iterations": n,
        "mean":  float(sims.mean()),
        "p50":   float(np.percentile(sims, 50)),
        "p80":   float(np.percentile(sims, 80)),
        "p95":   float(np.percentile(sims, 95)),
        "prob_overrun_baseline": float((sims > PROGRAM_BASELINE_USD).mean()),
    }])


# ----------------------------------------------------------------------------
# Schedule simulation
# ----------------------------------------------------------------------------
def simulate_schedule(
    n: int = N_ITERATIONS_DEFAULT,
    rng_seed: int = RANDOM_SEED + 1,  # decouple from cost stream
    mitigations: dict | None = None,
) -> np.ndarray:
    """
    Simulate N possible LRR slip outcomes, in days.

    Each iteration:
        lrr_slip = current_forecast_slip
                 + sum_ws( triangular(SCHEDULE_DRIFT_MIN, 0, MAX) )
                 + sum_risk( triggered ? impact_schedule_days : 0 )
    """
    rng = np.random.default_rng(rng_seed)
    risks = _load_open_risks()
    mitigations = mitigations or {}

    # Current forecast LRR slip (deterministic anchor).
    lrr = launch_readiness_forecast().iloc[0]
    current_slip = int(lrr["slip_days"])

    # Workstream-level execution drift. 8 workstreams.
    n_ws = 8
    drift = rng.triangular(
        left=SCHEDULE_DRIFT_MIN, mode=SCHEDULE_DRIFT_MODE,
        right=SCHEDULE_DRIFT_MAX, size=(n, n_ws),
    ).sum(axis=1)
    # The 8 workstreams don't all push 1:1 into LRR -- only the critical path
    # does. We use a fractional flow-through to be honest about this.
    LRR_FLOW_THROUGH = 0.35
    drift = drift * LRR_FLOW_THROUGH

    # Discrete risks.
    risk_days = np.zeros(n)
    for _, r in risks.iterrows():
        red = mitigations.get(r["risk_id"], 0.0)
        adj_prob = r["probability"] * (1 - red)
        fires = rng.random(n) < adj_prob
        # Same flow-through logic: not every risk's schedule impact directly
        # extends LRR, but for risks on launch-gating workstreams it does.
        # We apply a 0.6 flow-through to be conservative.
        RISK_LRR_FLOW = 0.6
        risk_days += fires * r["impact_schedule_days"] * RISK_LRR_FLOW

    return current_slip + drift + risk_days


def schedule_distribution_summary(
    n: int = N_ITERATIONS_DEFAULT,
    mitigations: dict | None = None,
) -> pd.DataFrame:
    sims = simulate_schedule(n=n, mitigations=mitigations)
    return pd.DataFrame([{
        "iterations": n,
        "mean_days":  float(sims.mean()),
        "p50_days":   float(np.percentile(sims, 50)),
        "p80_days":   float(np.percentile(sims, 80)),
        "p95_days":   float(np.percentile(sims, 95)),
        "p50_weeks":  float(np.percentile(sims, 50) / 7),
        "p80_weeks":  float(np.percentile(sims, 80) / 7),
        "p95_weeks":  float(np.percentile(sims, 95) / 7),
    }])


# ----------------------------------------------------------------------------
# Per-risk expected contribution (for explaining the tail)
# ----------------------------------------------------------------------------
def risk_contribution_table() -> pd.DataFrame:
    """
    Expected $ and day contribution per risk = probability * impact.
    This decomposes the Monte Carlo tail back to the risks driving it.
    """
    risks = _load_open_risks()
    risks["expected_cost_usd"] = risks["probability"] * risks["impact_cost_usd"]
    risks["expected_days"] = risks["probability"] * risks["impact_schedule_days"]
    return (
        risks.sort_values("expected_cost_usd", ascending=False)
        [["risk_id", "title", "probability",
          "impact_cost_usd", "impact_schedule_days",
          "expected_cost_usd", "expected_days"]]
        .reset_index(drop=True)
    )


# ----------------------------------------------------------------------------
# Executive printout
# ----------------------------------------------------------------------------
def print_executive_summary(n: int = N_ITERATIONS_DEFAULT) -> None:
    print("=" * 72)
    print(f"  MONTE CARLO FORECAST  --  {n:,} iterations, seed={RANDOM_SEED}")
    print("=" * 72)

    cost = cost_distribution_summary(n).iloc[0]
    print("  COST AT COMPLETION")
    print(f"    Baseline (BAC):        {fmt_usd(PROGRAM_BASELINE_USD)}")
    print(f"    Mean forecast:         {fmt_usd(cost['mean'])}")
    print(f"    P50 (median):          {fmt_usd(cost['p50'])}")
    print(f"    P80 (planning value):  {fmt_usd(cost['p80'])}    "
          f"<-- typical funding reserve target")
    print(f"    P95 (conservative):    {fmt_usd(cost['p95'])}")
    print(f"    P(overrun baseline):   {cost['prob_overrun_baseline']*100:.1f}%")
    print()

    sched = schedule_distribution_summary(n).iloc[0]
    print("  LAUNCH READINESS REVIEW (LRR) SLIP")
    print(f"    P50: {sched['p50_days']:>5.0f} days ({sched['p50_weeks']:>4.1f} weeks)")
    print(f"    P80: {sched['p80_days']:>5.0f} days ({sched['p80_weeks']:>4.1f} weeks)")
    print(f"    P95: {sched['p95_days']:>5.0f} days ({sched['p95_weeks']:>4.1f} weeks)")
    print()

    print("-" * 72)
    print("  TOP RISKS BY EXPECTED CONTRIBUTION")
    print("-" * 72)
    print(f"  {'Risk':<6} {'Title':<42} {'E[$]':>10} {'E[days]':>9}")
    for _, r in risk_contribution_table().head(6).iterrows():
        title = r["title"][:42]
        print(
            f"  {r['risk_id']:<6} {title:<42} "
            f"{fmt_usd(r['expected_cost_usd']):>10} "
            f"{r['expected_days']:>8.1f}d"
        )
    print("=" * 72)


# ----------------------------------------------------------------------------
# Demo
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    print_executive_summary()