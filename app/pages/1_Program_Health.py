"""
Program Health page.

Executive question: Is the program on track?

Goes one layer deeper than the home page: monthly cumulative S-curves
(PV / EV / AC) and the CPI / SPI trend over time. This is the single best
visual for spotting *when* the program went off plan.
"""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st
import plotly.graph_objects as go

from app.utils import (
    page_header, apply_layout, COLORS,
    load_program_summary, load_monthly_trends,
    load_workstream_summary,
)
from src.config import fmt_usd, PROGRAM_BASELINE_USD, PATHOLOGY_START_MONTH


st.set_page_config(page_title="Program Health", page_icon="📈", layout="wide")
page_header("📈 Program Health", "Is the program on track?")

# Top-line numbers
prog = load_program_summary().iloc[0]
c1, c2, c3, c4 = st.columns(4)
c1.metric("BAC", fmt_usd(PROGRAM_BASELINE_USD))
c2.metric("EAC", fmt_usd(prog["eac"]),
          delta=fmt_usd(prog["vac"]), delta_color="normal")
c3.metric("CPI", f"{prog['cpi']:.3f}",
          delta=f"{(prog['cpi']-1)*100:+.1f}%", delta_color="normal")
c4.metric("SPI", f"{prog['spi']:.3f}",
          delta=f"{(prog['spi']-1)*100:+.1f}%", delta_color="normal")

st.divider()

# S-curve: cumulative PV, EV, AC
st.subheader("Cumulative Earned Value S-Curve")
st.caption(
    "Where the lines separate is where performance diverged from plan. "
    "AC rising above PV with EV below both is the classic 'over budget and "
    "behind schedule' signature."
)

trends = load_monthly_trends()

fig = go.Figure()
fig.add_trace(go.Scatter(
    x=trends["period"], y=trends["pv_cum"],
    name="Planned Value", mode="lines",
    line=dict(color=COLORS["neutral"], width=2, dash="dash"),
))
fig.add_trace(go.Scatter(
    x=trends["period"], y=trends["ev_cum"],
    name="Earned Value", mode="lines",
    line=dict(color=COLORS["primary"], width=3),
))
ac_data = trends[trends["ac_cum"] > 0]
fig.add_trace(go.Scatter(
    x=ac_data["period"], y=ac_data["ac_cum"],
    name="Actual Cost", mode="lines",
    line=dict(color=COLORS["bad"], width=3),
))
pathology_start = trends["period"].iloc[PATHOLOGY_START_MONTH - 1]
fig.add_vline(
    x=pathology_start, line_dash="dot", line_color=COLORS["warn"],
    annotation_text="Pathologies emerge", annotation_position="top",
)
fig.update_yaxes(tickformat="$,.0s")
apply_layout(fig, height=450)
st.plotly_chart(fig, use_container_width=True)

# CPI/SPI trend
st.subheader("CPI and SPI Trends")
st.caption(
    "Cumulative indices over time. The 1.00 reference line is the on-plan "
    "threshold."
)

trends_with_metrics = trends[trends["ac_cum"] > 0]
fig2 = go.Figure()
fig2.add_trace(go.Scatter(
    x=trends_with_metrics["period"], y=trends_with_metrics["cpi"],
    name="CPI", mode="lines+markers",
    line=dict(color=COLORS["primary"], width=2),
))
fig2.add_trace(go.Scatter(
    x=trends_with_metrics["period"], y=trends_with_metrics["spi"],
    name="SPI", mode="lines+markers",
    line=dict(color=COLORS["warn"], width=2),
))
fig2.add_hline(y=1.0, line_dash="dash", line_color=COLORS["neutral"])
fig2.update_yaxes(range=[0.7, 1.15])
apply_layout(fig2, height=350)
st.plotly_chart(fig2, use_container_width=True)

# Workstream VAC bars
st.subheader("Where the Overrun Is Concentrated")
ws = load_workstream_summary()
fig3 = go.Figure(go.Bar(
    x=ws["workstream"],
    y=ws["vac"],
    marker_color=[
        COLORS["bad"] if v < -2e6 else
        (COLORS["warn"] if v < 0 else COLORS["good"])
        for v in ws["vac"]
    ],
    text=[fmt_usd(v) for v in ws["vac"]],
    textposition="outside",
))
fig3.update_yaxes(tickformat="$,.0s", title="Variance at Completion (VAC)")
apply_layout(fig3, height=400)
st.plotly_chart(fig3, use_container_width=True)

top3_pct = abs(ws.head(3)["vac"].sum() / ws["vac"].sum()) * 100
st.info(
    f"**Bottom line:** {ws.iloc[0]['workstream']}, "
    f"{ws.iloc[1]['workstream']}, and {ws.iloc[2]['workstream']} account "
    f"for {top3_pct:.0f}% of the projected program overrun. "
    f"Targeted mitigation on these three workstreams captures the bulk of "
    f"the exposure."
)