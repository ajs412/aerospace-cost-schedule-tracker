"""
Cost Performance page.

Executive question: Where is the money actually going wrong?
"""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px

from app.utils import (
    page_header, apply_layout, COLORS,
    load_workstream_summary, load_monthly_trends,
)
from src.config import fmt_usd
from src.analytics.evm import top_cost_drivers


st.set_page_config(page_title="Cost Performance", page_icon="💰", layout="wide")
page_header("💰 Cost Performance", "Where is the money actually going wrong?")

ws = load_workstream_summary()
trends = load_monthly_trends()

# Headline EVM numbers per workstream
st.subheader("Workstream EVM Detail")
display = ws.copy()
display["PV"]  = display["pv"].apply(fmt_usd)
display["EV"]  = display["ev"].apply(fmt_usd)
display["AC"]  = display["ac"].apply(fmt_usd)
display["BAC"] = display["bac"].apply(fmt_usd)
display["EAC"] = display["eac"].apply(fmt_usd)
display["VAC"] = display["vac"].apply(fmt_usd)
display["CPI"] = display["cpi"].round(3)
display["SPI"] = display["spi"].round(3)
st.dataframe(
    display[["workstream", "BAC", "PV", "EV", "AC", "CPI", "SPI", "EAC", "VAC"]]
        .rename(columns={"workstream": "Workstream"}),
    hide_index=True,
    use_container_width=True,
)

st.divider()

# CPI vs SPI scatter -- classic "quadrant" view for spotting concurrent
# cost and schedule problems
st.subheader("Cost vs. Schedule Performance Quadrant")
st.caption(
    "Top-right quadrant = healthy. Bottom-left = trouble. Bubble size is "
    "baseline budget (BAC), so big bubbles in the bottom-left command "
    "executive attention."
)

fig = go.Figure()
fig.add_trace(go.Scatter(
    x=ws["spi"], y=ws["cpi"],
    mode="markers+text",
    text=ws["workstream"],
    textposition="top center",
    marker=dict(
        size=ws["bac"] / 1e6,
        sizemode="area",
        sizeref=2 * ws["bac"].max() / 1e6 / 60**2,
        sizemin=8,
        color=ws["vac"],
        colorscale="RdYlGn",
        cmin=ws["vac"].min(), cmax=0,
        showscale=True,
        colorbar=dict(title="VAC ($)", tickformat="$,.0s"),
        line=dict(width=1, color="white"),
    ),
))
fig.add_hline(y=1.0, line_dash="dash", line_color=COLORS["neutral"])
fig.add_vline(x=1.0, line_dash="dash", line_color=COLORS["neutral"])
fig.update_xaxes(title="SPI (schedule performance)", range=[0.80, 1.05])
fig.update_yaxes(title="CPI (cost performance)", range=[0.80, 1.05])
apply_layout(fig, height=500)
st.plotly_chart(fig, use_container_width=True)

st.divider()

# Monthly burn rate (AC vs PV) -- where in time the divergence happens
st.subheader("Monthly Burn Rate vs. Plan")
st.caption(
    "Period (non-cumulative) actual cost vs. planned value. Bars sitting "
    "above the dashed line are months where the program spent more than "
    "planned."
)

ac_months = trends[trends["ac"] > 0]
fig2 = go.Figure()
fig2.add_trace(go.Bar(
    x=ac_months["period"], y=ac_months["ac"],
    name="Actual Cost", marker_color=COLORS["bad"], opacity=0.85,
))
fig2.add_trace(go.Scatter(
    x=ac_months["period"], y=ac_months["pv"],
    name="Planned Value", mode="lines+markers",
    line=dict(color=COLORS["neutral"], width=2, dash="dash"),
))
fig2.update_yaxes(tickformat="$,.0s")
apply_layout(fig2, height=400)
st.plotly_chart(fig2, use_container_width=True)

st.divider()

# Top cost drivers
st.subheader("Top Cost Drivers")
drivers = top_cost_drivers(5)
display_d = drivers.copy()
display_d["Cost Variance"] = display_d["cost_variance_usd"].apply(fmt_usd)
display_d["Forecast VAC"]  = display_d["vac"].apply(fmt_usd)
display_d["CPI"] = display_d["cpi"].round(3)
st.dataframe(
    display_d[["workstream", "CPI", "Cost Variance", "Forecast VAC"]]
        .rename(columns={"workstream": "Workstream"}),
    hide_index=True,
    use_container_width=True,
)

worst = drivers.iloc[0]
st.warning(
    f"**Largest cost driver:** {worst['workstream']} at CPI {worst['cpi']:.3f}. "
    f"Cost variance to date is {fmt_usd(worst['cost_variance_usd'])} with a "
    f"projected variance at completion of {fmt_usd(worst['vac'])}."
)