"""
Schedule Risk page.

Executive question: What's going to slip, and by how much?
"""

from __future__ import annotations

import streamlit as st
import plotly.graph_objects as go

from app.utils import (
    page_header, apply_layout, COLORS,
    load_milestone_slip, load_lrr_forecast,
    load_workstream_avg_slip, load_critical_path,
    load_propagation,
)
from src.config import fmt_usd


st.set_page_config(page_title="Schedule Risk", page_icon="🗓️", layout="wide")
page_header("🗓️ Schedule Risk", "What's going to slip, and by how much?")

# LRR forecast headline
lrr = load_lrr_forecast().iloc[0]
c1, c2, c3 = st.columns(3)
c1.metric("LRR baseline", str(lrr["baseline_date"]))
c2.metric("LRR forecast", str(lrr["forecast_date"]),
          delta=f"+{int(lrr['slip_days'])} days",
          delta_color="inverse")
c3.metric("Slip (weeks)", f"{lrr['slip_weeks']:.1f}",
          delta=lrr["status"], delta_color="off")

st.divider()

# Milestone slip waterfall
st.subheader("Milestone Slip")
st.caption(
    "Each bar is a milestone's slip (forecast or actual minus baseline). "
    "Sorted from largest slip to smallest."
)

ms = load_milestone_slip()
fig = go.Figure(go.Bar(
    y=ms["name"], x=ms["slip_days"],
    orientation="h",
    marker_color=[
        COLORS["bad"] if d > 30 else
        (COLORS["warn"] if d > 7 else COLORS["good"])
        for d in ms["slip_days"]
    ],
    text=[f"{int(d)}d" for d in ms["slip_days"]],
    textposition="outside",
))
fig.update_xaxes(title="Slip (days)")
fig.update_yaxes(autorange="reversed")
apply_layout(fig, height=450)
st.plotly_chart(fig, use_container_width=True)

st.divider()

# Workstream-level slip ranking
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("Slip by Workstream")
    st.caption("Cost-weighted average task slip per workstream.")
    ws_slip = load_workstream_avg_slip()
    fig2 = go.Figure(go.Bar(
        x=ws_slip["weighted_slip_days"],
        y=ws_slip["workstream"],
        orientation="h",
        marker_color=COLORS["primary"],
        text=[f"{v:.1f}d" for v in ws_slip["weighted_slip_days"]],
        textposition="outside",
    ))
    fig2.update_xaxes(title="Weighted slip (days)")
    fig2.update_yaxes(autorange="reversed")
    apply_layout(fig2, height=400)
    st.plotly_chart(fig2, use_container_width=True)

with col2:
    st.subheader("Upstream → I&T Propagation")
    st.caption(
        "How much of Integration & Test's slip is attributable to each "
        "upstream workstream."
    )
    prop = load_propagation()
    fig3 = go.Figure(go.Bar(
        x=prop["attributed_to_it_days"],
        y=prop["upstream_workstream"],
        orientation="h",
        marker_color=COLORS["warn"],
        text=[f"{v:.1f}d ({p:.0f}%)" for v, p
              in zip(prop["attributed_to_it_days"], prop["pct_of_it_slip"])],
        textposition="outside",
    ))
    fig3.update_xaxes(title="Days attributed to I&T slip")
    fig3.update_yaxes(autorange="reversed")
    apply_layout(fig3, height=400)
    st.plotly_chart(fig3, use_container_width=True)

st.divider()

# Critical path tasks
st.subheader("Forecast-Driven Critical Path")
st.caption(
    "Tasks with the latest forecast finish dates. These are the items that "
    "determine the program end date."
)
cp = load_critical_path(top_n=10)
display = cp.copy()
display["Baseline End"]     = display["baseline_end"].dt.strftime("%Y-%m-%d")
display["Forecast Finish"]  = display["forecast_finish"].dt.strftime("%Y-%m-%d")
display["Slip (days)"]      = display["forecast_slip_days"].astype(int)
display["% Complete"]       = (display["percent_complete"] * 100).round(0).astype(int)
st.dataframe(
    display[["task_id", "workstream", "name",
             "Baseline End", "Forecast Finish", "Slip (days)", "% Complete"]]
        .rename(columns={
            "task_id": "Task", "workstream": "Workstream", "name": "Description",
        }),
    hide_index=True,
    use_container_width=True,
)

st.info(
    f"**Bottom line:** {prop.iloc[0]['upstream_workstream']} alone is "
    f"attributable for {prop.iloc[0]['pct_of_it_slip']:.0f}% of Integration "
    f"& Test's slip. Even if I&T executes flawlessly from this point, the "
    f"upstream-inherited slip continues to drive LRR forecast outward."
)