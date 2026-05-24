"""
Risk Register page.

Executive question: What's in the risk register, and what's it worth?
"""

from __future__ import annotations

import streamlit as st
import plotly.graph_objects as go

from app.utils import (
    page_header, apply_layout, COLORS,
    load_risk_register, load_risk_contributions,
)
from src.config import fmt_usd


st.set_page_config(page_title="Risk Register", page_icon="⚠️", layout="wide")
page_header("⚠️ Risk Register",
            "What's in the risk register, and what's it worth?")

risks = load_risk_register()
contrib = load_risk_contributions()

# Top-line risk numbers
total_exposure = risks["exposure_usd"].sum()
open_count = (risks["status"] == "Open").sum()
mitigating_count = (risks["status"] == "Mitigating").sum()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Open risks", int(open_count))
c2.metric("Mitigating", int(mitigating_count))
c3.metric("Total exposure", fmt_usd(total_exposure))
c4.metric("Largest single risk", fmt_usd(risks["exposure_usd"].max()))

st.divider()

# 5x5 heat map -- the standard PMO / DoD risk view
st.subheader("5×5 Risk Heat Map")
st.caption(
    "Probability buckets (Low/Med-Low/Med/Med-High/High) × cost-impact "
    "buckets ($). Each cell shows the count of risks in that bucket."
)

# Bucket probability into 5 bins.
prob_bins = [0, 0.2, 0.4, 0.6, 0.8, 1.0]
prob_labels = ["Very Low", "Low", "Medium", "High", "Very High"]
risks["prob_bucket"] = (
    [prob_labels[min(int(p * 5), 4)] for p in risks["probability"]]
)
# Bucket impact into 5 bins by quintile of impact_cost_usd.
import pandas as pd
impact_bins = [0, 3e6, 6e6, 10e6, 15e6, float("inf")]
impact_labels = ["<$3M", "$3-6M", "$6-10M", "$10-15M", ">$15M"]
risks["impact_bucket"] = pd.cut(
    risks["impact_cost_usd"], bins=impact_bins, labels=impact_labels,
    include_lowest=True,
)

# Build the heat-map z-matrix.
heat = (
    risks.groupby(["impact_bucket", "prob_bucket"], observed=True)
    .size()
    .unstack(fill_value=0)
    .reindex(index=impact_labels, columns=prob_labels, fill_value=0)
)

fig = go.Figure(go.Heatmap(
    z=heat.values,
    x=heat.columns.tolist(),
    y=heat.index.tolist(),
    colorscale=[[0, "#f5f5f5"], [0.5, COLORS["warn"]], [1, COLORS["bad"]]],
    text=heat.values,
    texttemplate="%{text}",
    textfont=dict(size=14, color="black"),
    showscale=False,
))
fig.update_xaxes(title="Probability →")
fig.update_yaxes(title="Cost impact →")
apply_layout(fig, height=380)
st.plotly_chart(fig, use_container_width=True)

st.divider()

# Pareto: cumulative exposure
st.subheader("Risk Pareto — Cumulative Exposure")
st.caption(
    "Risks ranked by individual exposure (probability × impact). The line "
    "shows cumulative exposure as a percentage of the total."
)

pareto = contrib.head(10).copy()
pareto["cum_pct"] = (
    pareto["expected_cost_usd"].cumsum() / contrib["expected_cost_usd"].sum() * 100
)

fig2 = go.Figure()
fig2.add_trace(go.Bar(
    x=pareto["risk_id"],
    y=pareto["expected_cost_usd"],
    name="Expected $ contribution",
    marker_color=COLORS["primary"],
    text=[fmt_usd(v) for v in pareto["expected_cost_usd"]],
    textposition="outside",
))
fig2.add_trace(go.Scatter(
    x=pareto["risk_id"],
    y=pareto["cum_pct"],
    name="Cumulative %",
    yaxis="y2",
    mode="lines+markers",
    line=dict(color=COLORS["bad"], width=3),
))
fig2.update_layout(
    yaxis=dict(title="Expected $ contribution", tickformat="$,.0s"),
    yaxis2=dict(title="Cumulative %", overlaying="y", side="right",
                range=[0, 105], ticksuffix="%"),
)
apply_layout(fig2, height=420)
st.plotly_chart(fig2, use_container_width=True)

st.divider()

# Full risk register table
st.subheader("Open Risk Register")
display = risks.copy()
display["Prob"]     = (display["probability"] * 100).round(0).astype(int).astype(str) + "%"
display["Impact $"] = display["impact_cost_usd"].apply(fmt_usd)
display["Impact d"] = display["impact_schedule_days"].astype(int)
display["Exposure"] = display["exposure_usd"].apply(fmt_usd)
st.dataframe(
    display[["risk_id", "title", "workstream", "Prob", "Impact $",
             "Impact d", "Exposure", "status", "owner"]]
        .rename(columns={
            "risk_id": "ID", "title": "Title",
            "workstream": "Workstream", "status": "Status", "owner": "Owner",
        }),
    hide_index=True,
    use_container_width=True,
)

top3 = contrib.head(3)
top3_share = top3["expected_cost_usd"].sum() / contrib["expected_cost_usd"].sum() * 100
st.warning(
    f"**Pareto insight:** The top 3 risks "
    f"({', '.join(top3['risk_id'].tolist())}) account for {top3_share:.0f}% "
    f"of total expected risk exposure. Mitigating these three captures "
    f"the bulk of the program tail."
)