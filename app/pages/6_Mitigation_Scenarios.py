"""
Mitigation Scenarios page.

Executive question: What should we do about it?

This is the marquee interactive page. The user picks mitigations from a
checklist, the Monte Carlo reruns, and the page shows before/after
distributions with the delta highlighted.
"""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st
import numpy as np
import plotly.graph_objects as go

from app.utils import (
    page_header, apply_layout, COLORS,
    load_mitigation_catalog,
    run_cost_distribution, run_schedule_distribution,
)
from src.analytics.mitigation import evaluate_scenario
from src.config import fmt_usd, PROGRAM_BASELINE_USD


st.set_page_config(page_title="Mitigation Scenarios",
                   page_icon="🛠️", layout="wide")
page_header("🛠️ Mitigation Scenarios", "What should we do about it?")

cat = load_mitigation_catalog()

# ----------------------------------------------------------------------------
# Sidebar: mitigation picker
# ----------------------------------------------------------------------------
st.sidebar.header("Build a scenario")
st.sidebar.caption(
    "Select mitigations to apply. The Monte Carlo will rerun with your "
    "selections."
)

# Preset buttons -- one click to load a defined scenario.
preset = st.sidebar.radio(
    "Preset",
    options=["Custom", "Quick Wins (top-3 ROI)",
            "Top-3 Risks Addressed", "Full Mitigation Plan"],
    index=0,
)

quick_wins = cat.sort_values("roi_expected", ascending=False).head(3)
preset_map = {
    "Quick Wins (top-3 ROI)":  quick_wins["mitigation_id"].tolist(),
    "Top-3 Risks Addressed":   ["MIT001", "MIT002", "MIT005"],
    "Full Mitigation Plan":    cat["mitigation_id"].tolist(),
}
default_selection = preset_map.get(preset, [])

selected = st.sidebar.multiselect(
    "Mitigations",
    options=cat["mitigation_id"].tolist(),
    default=default_selection,
    format_func=lambda mid: (
        f"{mid} — {cat[cat['mitigation_id']==mid]['description'].iloc[0][:38]}..."
    ),
)

if not selected:
    st.info("Select one or more mitigations from the sidebar to see the impact.")
    st.stop()

# ----------------------------------------------------------------------------
# Run the scenario
# ----------------------------------------------------------------------------
# evaluate_scenario uses paired seeds for variance reduction so the before/after
# comparison is apples-to-apples (same random risk-firing pattern in both).
result = evaluate_scenario(selected, scenario_name=preset).iloc[0]

# Headline before/after
st.subheader("Headline Before vs. After")
c1, c2, c3, c4 = st.columns(4)
c1.metric(
    "P80 cost",
    fmt_usd(result["cost_p80_after"]),
    delta=fmt_usd(-result["cost_p80_reduction"]),
    delta_color="normal",
    help="Monte Carlo 80th-percentile final cost.",
)
c2.metric(
    "P80 LRR slip",
    f"{result['sched_p80_weeks_after']:.1f} wks",
    delta=f"-{result['sched_p80_weeks_recov']:.1f} weeks",
    delta_color="inverse",
)
c3.metric(
    "Investment",
    fmt_usd(result["mitigation_cost_usd"]),
    help="Total cost of the selected mitigations.",
)
c4.metric(
    "ROI (P80 $)",
    f"{result['roi_p80_cost']:.1f}x",
    help="P80 cost reduction divided by mitigation cost.",
)

st.divider()

# ----------------------------------------------------------------------------
# Distribution overlay: before vs. after cost
# ----------------------------------------------------------------------------
st.subheader("Cost Distribution: Before vs. After")
st.caption(
    "10,000-iteration Monte Carlo. Dashed lines mark P80 of each distribution. "
    "The leftward shift of the histogram is the mitigation effect."
)

# Tuples are hashable -> cache-friendly.
cost_before = run_cost_distribution(())
cost_after  = run_cost_distribution(tuple(sorted(selected)))

fig = go.Figure()
fig.add_trace(go.Histogram(
    x=cost_before / 1e6, name="Before", marker_color=COLORS["bad"],
    opacity=0.6, nbinsx=60,
))
fig.add_trace(go.Histogram(
    x=cost_after / 1e6, name="After", marker_color=COLORS["good"],
    opacity=0.6, nbinsx=60,
))
fig.add_vline(
    x=np.percentile(cost_before, 80) / 1e6,
    line_dash="dash", line_color=COLORS["bad"],
    annotation_text="P80 before", annotation_position="top",
)
fig.add_vline(
    x=np.percentile(cost_after, 80) / 1e6,
    line_dash="dash", line_color=COLORS["good"],
    annotation_text="P80 after", annotation_position="bottom",
)
fig.add_vline(
    x=PROGRAM_BASELINE_USD / 1e6,
    line_dash="dot", line_color=COLORS["neutral"],
    annotation_text="Baseline", annotation_position="top left",
)
fig.update_xaxes(title="Final program cost ($M)")
fig.update_yaxes(title="Frequency")
fig.update_layout(barmode="overlay")
apply_layout(fig, height=420)
st.plotly_chart(fig, use_container_width=True)

# ----------------------------------------------------------------------------
# Distribution overlay: before vs. after schedule
# ----------------------------------------------------------------------------
st.subheader("LRR Slip Distribution: Before vs. After")

sched_before = run_schedule_distribution(())
sched_after  = run_schedule_distribution(tuple(sorted(selected)))

fig2 = go.Figure()
fig2.add_trace(go.Histogram(
    x=sched_before / 7, name="Before", marker_color=COLORS["bad"],
    opacity=0.6, nbinsx=50,
))
fig2.add_trace(go.Histogram(
    x=sched_after / 7, name="After", marker_color=COLORS["good"],
    opacity=0.6, nbinsx=50,
))
fig2.add_vline(
    x=np.percentile(sched_before, 80) / 7,
    line_dash="dash", line_color=COLORS["bad"],
    annotation_text="P80 before", annotation_position="top",
)
fig2.add_vline(
    x=np.percentile(sched_after, 80) / 7,
    line_dash="dash", line_color=COLORS["good"],
    annotation_text="P80 after", annotation_position="bottom",
)
fig2.update_xaxes(title="LRR slip (weeks)")
fig2.update_yaxes(title="Frequency")
fig2.update_layout(barmode="overlay")
apply_layout(fig2, height=420)
st.plotly_chart(fig2, use_container_width=True)

st.divider()

# ----------------------------------------------------------------------------
# Detail table
# ----------------------------------------------------------------------------
st.subheader("Selected Mitigations")
detail = cat[cat["mitigation_id"].isin(selected)].copy()
detail["Cost"]   = detail["cost_usd"].apply(fmt_usd)
detail["Red %"]  = (detail["reduction_pct"] * 100).round(0).astype(int).astype(str) + "%"
detail["E[Δ$]"]  = detail["expected_risk_reduction_usd"].apply(fmt_usd)
detail["E[ROI]"] = detail["roi_expected"].round(1).astype(str) + "x"
st.dataframe(
    detail[["mitigation_id", "risk_id", "description",
            "Cost", "Red %", "E[Δ$]", "E[ROI]", "status"]]
        .rename(columns={
            "mitigation_id": "Mitigation", "risk_id": "Risk",
            "description": "Description", "status": "Status",
        }),
    hide_index=True,
    use_container_width=True,
)

# ----------------------------------------------------------------------------
# Resume-bullet narrative
# ----------------------------------------------------------------------------
st.divider()
st.subheader("The Story This Tells")

st.markdown(
    f"""
The selected mitigation set costs **{fmt_usd(result['mitigation_cost_usd'])}**
to implement. Re-running the cost Monte Carlo (10,000 iterations, paired seeds
for variance reduction) shows the P80 final cost dropping from
**{fmt_usd(result['cost_p80_before'])}** to **{fmt_usd(result['cost_p80_after'])}**
— a **{fmt_usd(result['cost_p80_reduction'])}** reduction in tail risk for a
**{result['roi_p80_cost']:.1f}x return**.

On the schedule side, the P80 LRR slip drops from
**{result['sched_p80_weeks_before']:.1f} weeks** to
**{result['sched_p80_weeks_after']:.1f} weeks**, recovering
**{result['sched_p80_weeks_recov']:.1f} weeks** of launch readiness.
"""
)