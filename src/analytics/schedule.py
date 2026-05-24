"""
Schedule risk analytics.

Aerospace programs are usually governed by a small number of executive-level
milestones (PDR, CDR, TRR, FRR, LRR). What matters at the leadership level is:

    * Which milestones are slipping, and by how much?
    * What is the *forecast* delay to Launch Readiness Review (LRR)?
    * Which workstreams are dragging the program?
    * Where is upstream slip propagating into Integration & Test?

The task graph in this v1 schema chains each task to the previous task within
its workstream (predecessor_ids is a single TEXT field). That is enough to
compute task-level slip and a workstream-level "longest residual chain"
proxy for the critical path. A fully cross-workstream CTE-based critical path
is a Phase 3 extension once we normalize task_dependencies.

Outputs:
    * milestone_slip()          -> one row per milestone, slip in days
    * workstream_avg_slip()     -> mean task slip per workstream
    * critical_path_tasks()     -> longest residual task chain per workstream
    * launch_readiness_forecast() -> single-row LRR slip summary
    * upstream_to_it_propagation() -> how upstream WS slip lands in I&T

Run:
    python -m src.analytics.schedule
"""

from __future__ import annotations

import pandas as pd

from src.db import get_conn
from src.config import fmt_usd  # noqa: F401  (kept for symmetry / future use)


# ----------------------------------------------------------------------------
# Data load
# ----------------------------------------------------------------------------
def _load_tasks() -> pd.DataFrame:
    sql = """
        SELECT
            t.task_id,
            t.workstream_id,
            w.name AS workstream,
            t.name,
            t.predecessor_ids,
            t.baseline_start,
            t.baseline_end,
            t.actual_start,
            t.actual_end,
            t.baseline_cost_usd,
            t.percent_complete
        FROM tasks t
        JOIN workstreams w ON w.workstream_id = t.workstream_id
    """
    with get_conn() as conn:
        df = pd.read_sql_query(
            sql,
            conn,
            parse_dates=["baseline_start", "baseline_end",
                         "actual_start", "actual_end"],
        )
    return df


def _load_milestones() -> pd.DataFrame:
    sql = """
        SELECT
            m.milestone_id,
            m.name,
            m.workstream_id,
            w.name AS workstream,
            m.baseline_date,
            m.forecast_date,
            m.actual_date,
            m.status
        FROM milestones m
        LEFT JOIN workstreams w ON w.workstream_id = m.workstream_id
    """
    with get_conn() as conn:
        df = pd.read_sql_query(
            sql,
            conn,
            parse_dates=["baseline_date", "forecast_date", "actual_date"],
        )
    return df


# ----------------------------------------------------------------------------
# Milestone analytics
# ----------------------------------------------------------------------------
def milestone_slip() -> pd.DataFrame:
    """
    Slip in days for each milestone. For completed milestones, slip is
    actual - baseline. For open milestones, slip is forecast - baseline.
    Positive = behind plan.
    """
    df = _load_milestones()
    eff = df["actual_date"].fillna(df["forecast_date"])
    df["effective_date"] = eff
    df["slip_days"] = (eff - df["baseline_date"]).dt.days
    df["slip_weeks"] = (df["slip_days"] / 7).round(1)
    return df[[
        "milestone_id", "name", "workstream",
        "baseline_date", "forecast_date", "actual_date",
        "status", "slip_days", "slip_weeks",
    ]].sort_values("slip_days", ascending=False).reset_index(drop=True)


def launch_readiness_forecast() -> pd.DataFrame:
    """One-row summary focused on LRR (M12), which gates launch."""
    ms = milestone_slip()
    lrr = ms[ms["milestone_id"] == "M12"].iloc[0]
    return pd.DataFrame([{
        "milestone": lrr["name"],
        "baseline_date": lrr["baseline_date"].date(),
        "forecast_date": lrr["forecast_date"].date(),
        "slip_days": int(lrr["slip_days"]),
        "slip_weeks": float(lrr["slip_weeks"]),
        "status": lrr["status"],
    }])


# ----------------------------------------------------------------------------
# Task-level analytics
# ----------------------------------------------------------------------------
def _task_slip(df: pd.DataFrame) -> pd.Series:
    """
    Per-task slip in days. Uses actual_end when available; otherwise uses
    today's progress projection: baseline_end shifted by (1 - pct_complete)
    of the actual run-rate vs. baseline duration. For simplicity in v1 we
    use (actual_start - baseline_start) for in-flight tasks, which is the
    incurred slip so far.
    """
    incurred = (df["actual_end"] - df["baseline_end"]).dt.days
    in_flight_start_slip = (df["actual_start"] - df["baseline_start"]).dt.days
    return incurred.fillna(in_flight_start_slip).fillna(0).astype(int)


def workstream_avg_slip() -> pd.DataFrame:
    """Average task slip per workstream, in days, weighted by baseline cost."""
    df = _load_tasks()
    df["slip_days"] = _task_slip(df)
    grouped = (
        df.groupby("workstream")
        .apply(lambda g: pd.Series({
            "tasks": len(g),
            "avg_slip_days": g["slip_days"].mean(),
            "weighted_slip_days": (
                (g["slip_days"] * g["baseline_cost_usd"]).sum()
                / g["baseline_cost_usd"].sum()
            ),
            "max_slip_days": g["slip_days"].max(),
        }), include_groups=False)
        .reset_index()
    )
    return grouped.sort_values("weighted_slip_days", ascending=False).reset_index(drop=True)


def critical_path_tasks(top_n: int = 10) -> pd.DataFrame:
    """
    The v1 "critical path" view: among in-flight or upcoming tasks, the ones
    whose remaining work + accumulated slip drives the latest forecast finish.

    Forecast finish for a task:
        * complete -> actual_end
        * in flight -> actual_start + baseline_duration / max(pct, 0.05)
        * not started -> baseline_end + workstream's median slip

    Returns the top_n tasks by forecast_finish, descending.
    """
    df = _load_tasks().copy()
    df["baseline_duration"] = (df["baseline_end"] - df["baseline_start"]).dt.days

    # Workstream median slip used as a back-fill for not-yet-started tasks.
    df["task_slip_days"] = _task_slip(df)
    ws_median_slip = df.groupby("workstream")["task_slip_days"].median().to_dict()

    def forecast_finish(row):
        if pd.notna(row["actual_end"]):
            return row["actual_end"]
        if pd.notna(row["actual_start"]):
            # Forecast remaining duration from observed progress, capped at 3x
            # baseline duration. The cap prevents recently-started tasks (low
            # pct_complete) from producing wildly long projections that
            # dominate the critical path view.
            pct = max(row["percent_complete"], 0.05)
            dur = row["baseline_duration"]
            projected = int(min(dur / pct, dur * 3))
            return row["actual_start"] + pd.Timedelta(days=projected)
        # Not started: use baseline_end + workstream median slip.
        return row["baseline_end"] + pd.Timedelta(
            days=int(ws_median_slip.get(row["workstream"], 0))
        )

    df["forecast_finish"] = df.apply(forecast_finish, axis=1)
    df["forecast_slip_days"] = (df["forecast_finish"] - df["baseline_end"]).dt.days

    cols = [
        "task_id", "workstream", "name",
        "baseline_end", "forecast_finish", "forecast_slip_days",
        "percent_complete",
    ]
    return (
        df.sort_values("forecast_finish", ascending=False)
        .head(top_n)[cols]
        .reset_index(drop=True)
    )


# ----------------------------------------------------------------------------
# Propagation: upstream slip -> Integration & Test
# ----------------------------------------------------------------------------
# In a LEO satellite program, I&T is downstream of essentially every hardware
# and software workstream. When Payload, Avionics, or Thermal slip, that slip
# arrives at the I&T door. This analytic quantifies which upstreams are
# pushing I&T off plan.
UPSTREAM_TO_IT = {
    "Payload":         0.30,
    "Avionics":        0.25,
    "Thermal":         0.20,
    "Propulsion":      0.10,
    "Ground Software": 0.10,
    "Structures":      0.05,
}


def upstream_to_it_propagation() -> pd.DataFrame:
    """
    Estimate how much of I&T's slip is attributable to each upstream
    workstream. Uses weighted upstream slip * dependency weight as the
    attribution heuristic. This is a defensible v1 model -- in production
    this would come from a proper dependency network walk.
    """
    ws = workstream_avg_slip().set_index("workstream")
    it_slip = float(ws.loc["Integration & Test", "weighted_slip_days"])

    rows = []
    for upstream, weight in UPSTREAM_TO_IT.items():
        if upstream not in ws.index:
            continue
        upstream_slip = float(ws.loc[upstream, "weighted_slip_days"])
        attributed = upstream_slip * weight
        rows.append({
            "upstream_workstream": upstream,
            "upstream_slip_days": round(upstream_slip, 1),
            "dependency_weight": weight,
            "attributed_to_it_days": round(attributed, 1),
        })

    out = pd.DataFrame(rows).sort_values("attributed_to_it_days", ascending=False)
    out = out.reset_index(drop=True)
    total_attributed = out["attributed_to_it_days"].sum()
    out["pct_of_it_slip"] = (
        out["attributed_to_it_days"] / it_slip * 100 if it_slip else 0
    ).round(1)
    return out


# ----------------------------------------------------------------------------
# Executive printout
# ----------------------------------------------------------------------------
def print_executive_summary() -> None:
    print("=" * 70)
    print("  SCHEDULE RISK EXECUTIVE SUMMARY")
    print("=" * 70)

    lrr = launch_readiness_forecast().iloc[0]
    print(f"  Launch Readiness Review forecast slip: "
          f"{lrr['slip_days']} days ({lrr['slip_weeks']:.1f} weeks)")
    print(f"  Baseline LRR: {lrr['baseline_date']}   "
          f"Forecast LRR: {lrr['forecast_date']}")
    print(f"  Status: {lrr['status']}")
    print()

    print("-" * 70)
    print("  MILESTONE SLIP  (top 6 by days)")
    print("-" * 70)
    ms = milestone_slip().head(6)
    print(f"  {'Milestone':<38} {'Status':<10} {'Slip (wks)':>10}")
    for _, r in ms.iterrows():
        print(f"  {r['name']:<38} {r['status']:<10} {r['slip_weeks']:>10.1f}")
    print()

    print("-" * 70)
    print("  WORKSTREAM AVERAGE SLIP  (cost-weighted)")
    print("-" * 70)
    print(f"  {'Workstream':<22} {'Tasks':>6} {'Avg Slip':>10} {'Max Slip':>10}")
    for _, r in workstream_avg_slip().iterrows():
        print(
            f"  {r['workstream']:<22} {int(r['tasks']):>6} "
            f"{r['weighted_slip_days']:>8.1f}d {int(r['max_slip_days']):>8}d"
        )
    print()

    print("-" * 70)
    print("  CRITICAL PATH  (latest forecast finish)")
    print("-" * 70)
    cp = critical_path_tasks(8)
    print(f"  {'Task':<10} {'Workstream':<22} {'Forecast Finish':<16} {'Slip':>6}")
    for _, r in cp.iterrows():
        print(
            f"  {r['task_id']:<10} {r['workstream']:<22} "
            f"{str(r['forecast_finish'].date()):<16} "
            f"{int(r['forecast_slip_days']):>5}d"
        )
    print()

    print("-" * 70)
    print("  UPSTREAM SLIP PROPAGATION INTO INTEGRATION & TEST")
    print("-" * 70)
    prop = upstream_to_it_propagation()
    print(f"  {'Upstream':<20} {'Slip':>8} {'Wt.':>6} "
          f"{'Attributed':>12} {'% of I&T':>10}")
    for _, r in prop.iterrows():
        print(
            f"  {r['upstream_workstream']:<20} "
            f"{r['upstream_slip_days']:>7.1f}d "
            f"{r['dependency_weight']:>6.2f} "
            f"{r['attributed_to_it_days']:>10.1f}d "
            f"{r['pct_of_it_slip']:>9.1f}%"
        )
    print("=" * 70)


# ----------------------------------------------------------------------------
# Demo
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    print_executive_summary()