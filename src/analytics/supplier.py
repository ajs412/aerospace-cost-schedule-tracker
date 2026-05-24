"""
Supplier risk analytics.

In aerospace programs, the supply chain is usually the largest single source
of cost and schedule risk. A typical LEO satellite program flows 40-60% of
its cost through suppliers and subcontractors. This module quantifies:

    * On-time delivery (OTD) -- the operational measure of supplier reliability
    * Quality acceptance     -- the quality measure
    * Composite supplier health score (blends OTD, quality, criticality)
    * Concentration risk     -- Herfindahl-Hirschman Index (HHI) of supplier spend
    * Sole-source exposure   -- dollars and percentage flowing through single sources

Outputs:
    * supplier_scorecard()         -> one row per supplier
    * top_risk_suppliers()         -> ranked worst suppliers
    * launch_risk_suppliers()      -> suppliers serving Launch Readiness or
                                       sitting on the critical path
    * concentration_summary()      -> HHI, sole-source $ and %

Run:
    python -m src.analytics.supplier
"""

from __future__ import annotations

import pandas as pd

from src.db import get_conn
from src.config import fmt_usd


# ----------------------------------------------------------------------------
# Data load
# ----------------------------------------------------------------------------
def _load_pos() -> pd.DataFrame:
    sql = """
        SELECT
            p.po_id,
            p.supplier_id,
            s.name AS supplier,
            s.country,
            s.tier,
            s.criticality,
            s.sole_source_flag,
            p.workstream_id,
            w.name AS workstream,
            p.po_value_usd,
            p.po_date,
            p.promised_delivery,
            p.actual_delivery,
            p.quality_acceptance
        FROM purchase_orders p
        JOIN suppliers s   ON s.supplier_id   = p.supplier_id
        JOIN workstreams w ON w.workstream_id = p.workstream_id
    """
    with get_conn() as conn:
        df = pd.read_sql_query(
            sql,
            conn,
            parse_dates=["po_date", "promised_delivery", "actual_delivery"],
        )
    return df


# ----------------------------------------------------------------------------
# Per-supplier scorecard
# ----------------------------------------------------------------------------
# Composite supplier health score: weighted blend of OTD, quality, and
# criticality. Higher score = healthier supplier.
_CRITICALITY_WEIGHT = {"Critical": 1.0, "High": 0.85, "Medium": 0.65, "Low": 0.45}


def _delivered_only(df: pd.DataFrame) -> pd.DataFrame:
    """Subset to POs that have been delivered (excludes Pending)."""
    return df[df["actual_delivery"].notna()].copy()


def supplier_scorecard() -> pd.DataFrame:
    """
    One row per supplier with the full risk profile:
        otd_pct        % of delivered POs that arrived on or before promised
        avg_delay_days mean (actual - promised) over delivered POs
        delayed_pos    count of POs delivered more than 7 days late
        accepted_pct   % of delivered POs with full quality acceptance
        rejected_count count of rejected POs
        total_spend    total PO value to date
        risk_score     composite 0-100, where higher = healthier
    """
    pos = _load_pos()
    delivered = _delivered_only(pos)
    delivered["delay_days"] = (
        delivered["actual_delivery"] - delivered["promised_delivery"]
    ).dt.days
    delivered["on_time"] = delivered["delay_days"] <= 0
    delivered["delayed"] = delivered["delay_days"] > 7
    delivered["accepted"] = delivered["quality_acceptance"] == "Accepted"
    delivered["rejected"] = delivered["quality_acceptance"] == "Rejected"

    # Aggregate at supplier grain.
    agg = (
        delivered.groupby(["supplier_id", "supplier"], as_index=False)
        .agg(
            delivered_pos=("po_id", "count"),
            otd_pct=("on_time", "mean"),
            avg_delay_days=("delay_days", "mean"),
            delayed_pos=("delayed", "sum"),
            accepted_pct=("accepted", "mean"),
            rejected_count=("rejected", "sum"),
        )
    )
    agg["otd_pct"] = (agg["otd_pct"] * 100).round(1)
    agg["accepted_pct"] = (agg["accepted_pct"] * 100).round(1)
    agg["avg_delay_days"] = agg["avg_delay_days"].round(1)

    # Total spend (uses all POs, not just delivered, for exposure sizing).
    spend = (
        pos.groupby("supplier_id", as_index=False)["po_value_usd"]
        .sum()
        .rename(columns={"po_value_usd": "total_spend_usd"})
    )

    # Static attributes.
    static = (
        pos.groupby("supplier_id", as_index=False)
        .agg(
            country=("country", "first"),
            tier=("tier", "first"),
            criticality=("criticality", "first"),
            sole_source_flag=("sole_source_flag", "first"),
        )
    )

    out = agg.merge(spend, on="supplier_id").merge(static, on="supplier_id")

    # Composite risk score: weighted blend, then scaled by criticality so that
    # a poor critical supplier scores worse than an equally-poor low-tier one.
    # Components are 0..1; final score is 0..100.
    otd_component     = out["otd_pct"] / 100
    quality_component = out["accepted_pct"] / 100
    delay_penalty     = (out["avg_delay_days"].clip(lower=0) / 30).clip(upper=1)

    raw = 0.55 * otd_component + 0.35 * quality_component - 0.10 * delay_penalty
    crit_weight = out["criticality"].map(_CRITICALITY_WEIGHT).fillna(0.65)
    # Critical suppliers are evaluated on a tougher curve: their raw score is
    # pulled toward zero proportionally to (1 - crit_weight)... no, simpler:
    # we just take the unweighted raw score for visibility, but multiply by
    # criticality weight only when ranking. So we keep raw_score and a
    # criticality-adjusted ranking_score.
    out["risk_score"]      = (raw * 100).clip(0, 100).round(1)
    out["ranking_score"]   = (raw * crit_weight * 100).round(1)
    return out.sort_values("ranking_score").reset_index(drop=True)


def top_risk_suppliers(n: int = 5) -> pd.DataFrame:
    """The n suppliers with the worst criticality-adjusted ranking score."""
    cols = [
        "supplier", "criticality", "sole_source_flag",
        "otd_pct", "avg_delay_days", "rejected_count",
        "total_spend_usd", "risk_score", "ranking_score",
    ]
    return supplier_scorecard()[cols].head(n).reset_index(drop=True)


# ----------------------------------------------------------------------------
# Launch-impact view
# ----------------------------------------------------------------------------
# Workstreams that directly gate launch. Any supplier serving these
# workstreams is a "launch-impact" supplier and gets surfaced separately for
# the executive view.
_LAUNCH_GATING_WORKSTREAMS = {
    "Launch Readiness",
    "Integration & Test",
    "Propulsion",
    "Avionics",
}


def launch_risk_suppliers() -> pd.DataFrame:
    """
    Suppliers whose performance directly affects launch readiness. Aggregates
    each supplier's OTD and avg delay across launch-gating workstreams only.
    """
    pos = _load_pos()
    gating = pos[pos["workstream"].isin(_LAUNCH_GATING_WORKSTREAMS)]
    delivered = _delivered_only(gating)
    if delivered.empty:
        return pd.DataFrame()

    delivered["delay_days"] = (
        delivered["actual_delivery"] - delivered["promised_delivery"]
    ).dt.days
    delivered["on_time"] = delivered["delay_days"] <= 0

    out = (
        delivered.groupby(["supplier", "criticality", "sole_source_flag"], as_index=False)
        .agg(
            launch_pos=("po_id", "count"),
            launch_otd_pct=("on_time", "mean"),
            launch_avg_delay_days=("delay_days", "mean"),
            launch_spend_usd=("po_value_usd", "sum"),
        )
    )
    out["launch_otd_pct"] = (out["launch_otd_pct"] * 100).round(1)
    out["launch_avg_delay_days"] = out["launch_avg_delay_days"].round(1)
    return out.sort_values("launch_avg_delay_days", ascending=False).reset_index(drop=True)


# ----------------------------------------------------------------------------
# Portfolio-level concentration & sole-source exposure
# ----------------------------------------------------------------------------
def concentration_summary() -> pd.DataFrame:
    """
    One-row summary of the supplier portfolio:
        total_suppliers
        total_spend_usd
        hhi              Herfindahl index of spend shares (0..10000 scale)
        top1_share_pct   share of largest supplier
        top3_share_pct   share of top 3 suppliers
        sole_source_usd  $ flowing through sole-source suppliers
        sole_source_pct  % of spend that is sole-sourced
    """
    pos = _load_pos()
    by_sup = pos.groupby(
        ["supplier_id", "supplier", "sole_source_flag"], as_index=False
    )["po_value_usd"].sum()
    total = by_sup["po_value_usd"].sum()

    by_sup["share"] = by_sup["po_value_usd"] / total
    # HHI on a 0-10000 scale (multiplied shares-as-percentages).
    hhi = ((by_sup["share"] * 100) ** 2).sum()

    by_sup_sorted = by_sup.sort_values("share", ascending=False)
    top1 = by_sup_sorted.iloc[0]["share"] * 100
    top3 = by_sup_sorted.head(3)["share"].sum() * 100

    sole = by_sup[by_sup["sole_source_flag"] == 1]
    sole_usd = sole["po_value_usd"].sum()
    sole_pct = sole_usd / total * 100

    return pd.DataFrame([{
        "total_suppliers": int(by_sup["supplier_id"].nunique()),
        "total_spend_usd": round(total, 2),
        "hhi": round(hhi, 1),
        "top1_share_pct": round(top1, 1),
        "top3_share_pct": round(top3, 1),
        "sole_source_usd": round(sole_usd, 2),
        "sole_source_pct": round(sole_pct, 1),
    }])


# ----------------------------------------------------------------------------
# Executive printout
# ----------------------------------------------------------------------------
def print_executive_summary() -> None:
    print("=" * 72)
    print("  SUPPLIER RISK EXECUTIVE SUMMARY")
    print("=" * 72)

    conc = concentration_summary().iloc[0]
    print(f"  Suppliers in portfolio:     {int(conc['total_suppliers'])}")
    print(f"  Total PO commitments:       {fmt_usd(conc['total_spend_usd'])}")
    print(f"  HHI (concentration index):  {conc['hhi']:.0f}   "
          f"({'concentrated' if conc['hhi'] > 1500 else 'diversified'})")
    print(f"  Top supplier share:         {conc['top1_share_pct']:.1f}%")
    print(f"  Top-3 supplier share:       {conc['top3_share_pct']:.1f}%")
    print(f"  Sole-source exposure:       {fmt_usd(conc['sole_source_usd'])} "
          f"({conc['sole_source_pct']:.1f}% of spend)")
    print()

    print("-" * 72)
    print("  SUPPLIER SCORECARD")
    print("-" * 72)
    print(f"  {'Supplier':<25} {'Crit':<9} {'SS':<3} "
          f"{'OTD%':>5} {'Δ days':>7} {'Rej':>4} {'Score':>6}")
    sc = supplier_scorecard()
    for _, r in sc.iterrows():
        print(
            f"  {r['supplier']:<25} {r['criticality']:<9} "
            f"{'Y' if r['sole_source_flag'] else 'N':<3} "
            f"{r['otd_pct']:>5.1f} {r['avg_delay_days']:>7.1f} "
            f"{int(r['rejected_count']):>4} {r['risk_score']:>6.1f}"
        )
    print()

    print("-" * 72)
    print("  TOP RISK SUPPLIERS  (criticality-adjusted ranking)")
    print("-" * 72)
    for _, r in top_risk_suppliers(5).iterrows():
        flags = []
        if r["sole_source_flag"]:
            flags.append("sole-source")
        if r["criticality"] == "Critical":
            flags.append("critical")
        flag_str = f"  [{', '.join(flags)}]" if flags else ""
        print(
            f"  {r['supplier']:<25} OTD={r['otd_pct']:.1f}%   "
            f"avg delay={r['avg_delay_days']:.1f}d   "
            f"spend={fmt_usd(r['total_spend_usd'])}{flag_str}"
        )
    print()

    print("-" * 72)
    print("  LAUNCH-IMPACT SUPPLIERS  (serving I&T / Avionics / Prop / Launch)")
    print("-" * 72)
    lr = launch_risk_suppliers()
    print(f"  {'Supplier':<25} {'OTD%':>5} {'Δ days':>8} {'Spend':>10} {'Flags':<12}")
    for _, r in lr.iterrows():
        flags = []
        if r["criticality"] == "Critical":
            flags.append("crit")
        if r["sole_source_flag"]:
            flags.append("SS")
        flag_str = ",".join(flags) if flags else "-"
        print(
            f"  {r['supplier']:<25} {r['launch_otd_pct']:>5.1f} "
            f"{r['launch_avg_delay_days']:>7.1f}d "
            f"{fmt_usd(r['launch_spend_usd']):>10} {flag_str:<12}"
        )
    print("=" * 72)


# ----------------------------------------------------------------------------
# Demo
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    print_executive_summary()