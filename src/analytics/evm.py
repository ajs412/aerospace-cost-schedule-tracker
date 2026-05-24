"""
Earned Value Management (EVM) analytics.

EVM is the standard cost/schedule control framework used across aerospace and
defense programs (DoD 5000.02, NASA NPR 7120.5, AS9100). It answers three
questions from three numbers:

    Planned Value (PV) -- the budgeted cost of work that *should* be done by now
    Earned Value (EV)  -- the budgeted cost of work that *has* been done
    Actual Cost (AC)   -- what we have actually spent to do it

From those three, the standard derived metrics are:

    CPI = EV / AC          Cost Performance Index (>1.0 = under budget)
    SPI = EV / PV          Schedule Performance Index (>1.0 = ahead of schedule)
    EAC = BAC / CPI        Estimate at Completion (independent forecast)
    VAC = BAC - EAC        Variance at Completion (negative = projected overrun)

Outputs:
    * program_summary()          -> single-row DataFrame, program-level rollup
    * workstream_summary()       -> one row per workstream
    * monthly_trends()           -> one row per (period, workstream)
    * top_cost_drivers()         -> ranked by absolute cost variance
    * top_schedule_drivers()     -> ranked by absolute schedule variance

Run:
    python -m src.analytics.evm
"""

from __future__ import annotations

import pandas as pd

from src.config import (
    PROGRAM_BASELINE_USD,
    PROGRAM_START,
    DATA_AS_OF_MONTH,
    fmt_usd,
)
from src.db import get_conn
from datetime import date


# ----------------------------------------------------------------------------
# Core data load
# ----------------------------------------------------------------------------
def _as_of_iso() -> str:
    """Return the data-as-of date in ISO format."""
    month_index = PROGRAM_START.month - 1 + DATA_AS_OF_MONTH
    return date(
        PROGRAM_START.year + month_index // 12,
        month_index % 12 + 1,
        1,
    ).isoformat()


def load_evm_facts() -> pd.DataFrame:
    """
    Pull the joined PV / EV / AC fact table at workstream + period grain.

    monthly_budget is the source of truth for PV; actual_spend supplies EV/AC.
    We LEFT JOIN budget -> actuals so future-period rows (PV but no actuals)
    survive for trend charts, then null-fill EV/AC to zero.
    """
    sql = """
        SELECT
            b.period,
            b.workstream_id,
            w.name AS workstream,
            w.baseline_budget_usd,
            b.planned_value_usd                  AS pv,
            COALESCE(a.earned_value_usd, 0.0)    AS ev,
            COALESCE(a.actual_cost_usd, 0.0)     AS ac
        FROM monthly_budget b
        JOIN workstreams w
          ON w.workstream_id = b.workstream_id
        LEFT JOIN actual_spend a
          ON a.period = b.period
         AND a.workstream_id = b.workstream_id
        ORDER BY b.period, b.workstream_id;
    """
    with get_conn() as conn:
        df = pd.read_sql_query(sql, conn, parse_dates=["period"])
    return df


# ----------------------------------------------------------------------------
# Metric helpers
# ----------------------------------------------------------------------------
def _safe_div(numer: float, denom: float) -> float:
    """Division that returns NaN on zero denominator (cleaner than ZeroDivision)."""
    return numer / denom if denom else float("nan")


def _derive_metrics(pv: float, ev: float, ac: float, bac: float) -> dict:
    """Compute CPI, SPI, EAC, VAC, and variances from the three EVM primitives."""
    cpi = _safe_div(ev, ac)
    spi = _safe_div(ev, pv)
    # EAC uses the CPI-based formula (the most common independent EAC).
    # When CPI is undefined (no actuals yet) we fall back to BAC.
    eac = _safe_div(bac, cpi) if cpi == cpi else bac  # NaN check
    vac = bac - eac
    return {
        "pv": pv,
        "ev": ev,
        "ac": ac,
        "bac": bac,
        "cpi": cpi,
        "spi": spi,
        "eac": eac,
        "vac": vac,
        "cost_variance_usd": ev - ac,        # CV  > 0 = under budget
        "schedule_variance_usd": ev - pv,    # SV  > 0 = ahead of schedule
    }


# ----------------------------------------------------------------------------
# Rollups
# ----------------------------------------------------------------------------
def program_summary(as_of: str | None = None) -> pd.DataFrame:
    """
    Program-level EVM rollup as of the given date (defaults to DATA_AS_OF_MONTH).
    Only periods strictly before as_of are counted as "performed to date."
    """
    as_of = as_of or _as_of_iso()
    df = load_evm_facts()
    to_date = df[df["period"] < pd.Timestamp(as_of)]

    pv = to_date["pv"].sum()
    ev = to_date["ev"].sum()
    ac = to_date["ac"].sum()
    metrics = _derive_metrics(pv, ev, ac, PROGRAM_BASELINE_USD)
    metrics["as_of"] = as_of
    return pd.DataFrame([metrics])


def workstream_summary(as_of: str | None = None) -> pd.DataFrame:
    """One EVM row per workstream, sorted by largest projected overrun first."""
    as_of = as_of or _as_of_iso()
    df = load_evm_facts()
    to_date = df[df["period"] < pd.Timestamp(as_of)]

    grouped = to_date.groupby(["workstream_id", "workstream"], as_index=False).agg(
        pv=("pv", "sum"),
        ev=("ev", "sum"),
        ac=("ac", "sum"),
        bac=("baseline_budget_usd", "first"),
    )

    derived = grouped.apply(
        lambda r: pd.Series(_derive_metrics(r["pv"], r["ev"], r["ac"], r["bac"])),
        axis=1,
    )
    out = pd.concat(
        [grouped[["workstream_id", "workstream"]], derived],
        axis=1,
    )
    # Sort by VAC ascending so the biggest projected overrun is at the top.
    return out.sort_values("vac").reset_index(drop=True)


def monthly_trends() -> pd.DataFrame:
    """
    Cumulative PV / EV / AC over time at the program level. Useful for the
    classic 'S-curve' EVM chart and for spotting the inflection where the
    pathologies kick in.
    """
    df = load_evm_facts()
    monthly = (
        df.groupby("period", as_index=False)[["pv", "ev", "ac"]]
        .sum()
        .sort_values("period")
    )
    monthly[["pv_cum", "ev_cum", "ac_cum"]] = monthly[["pv", "ev", "ac"]].cumsum()
    monthly["cpi"] = monthly["ev_cum"] / monthly["ac_cum"].replace(0, pd.NA)
    monthly["spi"] = monthly["ev_cum"] / monthly["pv_cum"].replace(0, pd.NA)
    return monthly


def top_cost_drivers(n: int = 5) -> pd.DataFrame:
    """Workstreams with the largest dollar cost variance (CV = EV - AC)."""
    ws = workstream_summary()
    return (
        ws.assign(abs_cv=ws["cost_variance_usd"].abs())
        .sort_values("cost_variance_usd")  # most negative first
        .head(n)[["workstream", "cpi", "cost_variance_usd", "vac"]]
        .reset_index(drop=True)
    )


def top_schedule_drivers(n: int = 5) -> pd.DataFrame:
    """Workstreams with the largest dollar schedule variance (SV = EV - PV)."""
    ws = workstream_summary()
    return (
        ws.assign(abs_sv=ws["schedule_variance_usd"].abs())
        .sort_values("schedule_variance_usd")
        .head(n)[["workstream", "spi", "schedule_variance_usd"]]
        .reset_index(drop=True)
    )


# ----------------------------------------------------------------------------
# Executive printout
# ----------------------------------------------------------------------------
def print_executive_summary() -> None:
    prog = program_summary().iloc[0]
    ws = workstream_summary()

    print("=" * 70)
    print(f"  EVM EXECUTIVE SUMMARY  --  as of {prog['as_of']}")
    print("=" * 70)
    print(f"  BAC (baseline):             {fmt_usd(prog['bac']):>12}")
    print(f"  PV  (planned to date):      {fmt_usd(prog['pv']):>12}")
    print(f"  EV  (earned to date):       {fmt_usd(prog['ev']):>12}")
    print(f"  AC  (actual to date):       {fmt_usd(prog['ac']):>12}")
    print(f"  CPI (cost performance):     {prog['cpi']:>12.3f}   "
          f"{'UNDER' if prog['cpi'] > 1 else 'OVER'} budget on work performed")
    print(f"  SPI (schedule performance): {prog['spi']:>12.3f}   "
          f"{'AHEAD' if prog['spi'] > 1 else 'BEHIND'} schedule")
    print(f"  EAC (forecast at compl.):   {fmt_usd(prog['eac']):>12}")
    print(f"  VAC (forecast variance):    {fmt_usd(prog['vac']):>12}   "
          f"({'overrun' if prog['vac'] < 0 else 'underrun'} of "
          f"{fmt_usd(abs(prog['vac']))})")
    print()
    print("-" * 70)
    print("  WORKSTREAM PERFORMANCE  (sorted by projected overrun)")
    print("-" * 70)
    print(f"  {'Workstream':<22} {'CPI':>6} {'SPI':>6} {'EAC':>10} {'VAC':>11}")
    for _, r in ws.iterrows():
        print(
            f"  {r['workstream']:<22} "
            f"{r['cpi']:>6.3f} {r['spi']:>6.3f} "
            f"{fmt_usd(r['eac']):>10} {fmt_usd(r['vac']):>11}"
        )
    print()
    print("-" * 70)
    print("  TOP COST DRIVERS  (worst Cost Variance)")
    print("-" * 70)
    for _, r in top_cost_drivers(3).iterrows():
        print(
            f"  {r['workstream']:<22} CPI={r['cpi']:.3f}   "
            f"CV={fmt_usd(r['cost_variance_usd'])}   "
            f"VAC={fmt_usd(r['vac'])}"
        )
    print()
    print("-" * 70)
    print("  TOP SCHEDULE DRIVERS  (worst Schedule Variance)")
    print("-" * 70)
    for _, r in top_schedule_drivers(3).iterrows():
        print(
            f"  {r['workstream']:<22} SPI={r['spi']:.3f}   "
            f"SV={fmt_usd(r['schedule_variance_usd'])}"
        )
    print("=" * 70)


# ----------------------------------------------------------------------------
# Demo
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    print_executive_summary()