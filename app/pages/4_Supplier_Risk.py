"""
Supplier Risk page.

Executive question: Which suppliers are putting the launch at risk?
"""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st
import plotly.graph_objects as go

from app.utils import (
    page_header, apply_layout, COLORS,
    load_supplier_scorecard, load_launch_suppliers, load_concentration,
)
from src.config import fmt_usd


st.set_page_config(page_title="Supplier Risk", page_icon="🏭", layout="wide")
page_header("🏭 Supplier Risk",
            "Which suppliers are putting the launch at risk?")

conc = load_concentration().iloc[0]
sc = load_supplier_scorecard()

# Portfolio-level concentration KPIs
c1, c2, c3, c4 = st.columns(4)
c1.metric("Suppliers", int(conc["total_suppliers"]))
c2.metric("PO commitments", fmt_usd(conc["total_spend_usd"]))
c3.metric("Sole-source $", fmt_usd(conc["sole_source_usd"]),
          delta=f"{conc['sole_source_pct']:.1f}% of spend",
          delta_color="off")
c4.metric("HHI", f"{int(conc['hhi'])}",
          delta="concentrated" if conc["hhi"] > 1500 else "diversified",
          delta_color="off",
          help="Herfindahl-Hirschman Index of supplier spend shares.")

st.divider()

# OTD vs avg delay bubble chart with criticality coloring
st.subheader("Supplier Health Map")
st.caption(
    "On-time delivery rate vs. average delivery delay. Bubble size = total "
    "PO spend; color = criticality. Critical suppliers in the bottom-right "
    "are the program's highest risks."
)

color_map = {"Critical": COLORS["bad"], "High": COLORS["warn"],
             "Medium": COLORS["secondary"], "Low": COLORS["neutral"]}

fig = go.Figure()
for crit in ["Critical", "High", "Medium", "Low"]:
    subset = sc[sc["criticality"] == crit]
    if subset.empty:
        continue
    fig.add_trace(go.Scatter(
        x=subset["otd_pct"], y=subset["avg_delay_days"],
        mode="markers+text",
        name=crit,
        text=subset["supplier"],
        textposition="top center",
        marker=dict(
            size=subset["total_spend_usd"] / 1e6,
            sizemode="area",
            sizeref=2 * sc["total_spend_usd"].max() / 1e6 / 50**2,
            sizemin=8,
            color=color_map[crit],
            line=dict(width=1, color="white"),
        ),
    ))
fig.update_xaxes(title="On-time delivery %", range=[-5, 105])
fig.update_yaxes(title="Average delay (days)")
apply_layout(fig, height=500)
st.plotly_chart(fig, use_container_width=True)

st.divider()

# Full scorecard table
st.subheader("Supplier Scorecard")
display = sc.copy()
display["SS"]      = display["sole_source_flag"].map({1: "Yes", 0: ""})
display["OTD %"]   = display["otd_pct"].round(1)
display["Avg Δ"]   = display["avg_delay_days"].round(1)
display["Rej"]     = display["rejected_count"].astype(int)
display["Spend"]   = display["total_spend_usd"].apply(fmt_usd)
display["Score"]   = display["risk_score"].round(1)
st.dataframe(
    display[["supplier", "criticality", "SS", "OTD %", "Avg Δ",
             "Rej", "Spend", "Score"]]
        .rename(columns={"supplier": "Supplier", "criticality": "Criticality"}),
    hide_index=True,
    use_container_width=True,
)

st.divider()

# Launch-impact suppliers
st.subheader("Launch-Impact Suppliers")
st.caption(
    "Suppliers serving launch-gating workstreams: Launch Readiness, "
    "Integration & Test, Propulsion, and Avionics."
)
lr = load_launch_suppliers()
display_lr = lr.copy()
display_lr["SS"]    = display_lr["sole_source_flag"].map({1: "Yes", 0: ""})
display_lr["OTD %"] = display_lr["launch_otd_pct"]
display_lr["Avg Δ"] = display_lr["launch_avg_delay_days"]
display_lr["Spend"] = display_lr["launch_spend_usd"].apply(fmt_usd)
st.dataframe(
    display_lr[["supplier", "criticality", "SS", "OTD %", "Avg Δ", "Spend"]]
        .rename(columns={"supplier": "Supplier", "criticality": "Criticality"}),
    hide_index=True,
    use_container_width=True,
)

worst = sc.iloc[0]
st.error(
    f"**Highest-risk supplier:** {worst['supplier']} "
    f"({worst['criticality']}{', sole-source' if worst['sole_source_flag'] else ''}) "
    f"— OTD {worst['otd_pct']:.0f}%, average delay {worst['avg_delay_days']:.1f} "
    f"days, total spend {fmt_usd(worst['total_spend_usd'])}. "
    f"This is the single largest supply-chain risk in the program."
)