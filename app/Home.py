"""
Streamlit entrypoint for the Aerospace Program Cost & Schedule Risk Tracker.

Run from the project root:

    streamlit run app/Home.py

The sidebar will populate automatically with the pages in app/pages/.
"""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from app.utils import (
    page_header, status_badge,
    load_program_summary, load_workstream_summary,
    load_lrr_forecast, load_concentration,
    load_cost_summary, load_schedule_summary,
)
from src.config import fmt_usd, PROGRAM_BASELINE_USD


# ----------------------------------------------------------------------------
# Page config -- must be the first Streamlit call
# ----------------------------------------------------------------------------
st.set_page_config(
    page_title="ORION-LEO-1 | Program Risk Tracker",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)

page_header(
    "🛰️ ORION-LEO-1 Program Risk Tracker",
    "Cost, schedule, supplier, and probabilistic risk analytics for a "
    "$512M LEO satellite development program.",
)

# ----------------------------------------------------------------------------
# Top-level KPI strip
# ----------------------------------------------------------------------------
prog = load_program_summary().iloc[0]
lrr  = load_lrr_forecast().iloc[0]
conc = load_concentration().iloc[0]
cost = load_cost_summary().iloc[0]
sched = load_schedule_summary().iloc[0]

st.subheader("Program Health")

# Six KPIs in a single row. delta_color="inverse" means a positive value
# renders red (used for slip days, where bigger = worse).
k1, k2, k3, k4, k5, k6 = st.columns(6)

k1.metric(
    "CPI",
    f"{prog['cpi']:.3f}",
    delta=f"{(prog['cpi']-1)*100:+.1f}% vs 1.00",
    delta_color="normal",
    help="Cost Performance Index. >1.0 = under budget on work performed.",
)
k2.metric(
    "SPI",
    f"{prog['spi']:.3f}",
    delta=f"{(prog['spi']-1)*100:+.1f}% vs 1.00",
    delta_color="normal",
    help="Schedule Performance Index. >1.0 = ahead of schedule.",
)
k3.metric(
    "Forecast EAC",
    fmt_usd(prog["eac"]),
    delta=fmt_usd(prog["vac"]),
    delta_color="normal",
    help="Estimate at Completion (CPI-based). Delta is Variance at Completion.",
)
k4.metric(
    "LRR slip",
    f"{lrr['slip_weeks']:.1f} wks",
    delta=f"{int(lrr['slip_days'])} days",
    delta_color="inverse",
    help="Launch Readiness Review forecast slip vs. baseline.",
)
k5.metric(
    "Sole-source $",
    fmt_usd(conc["sole_source_usd"]),
    delta=f"{conc['sole_source_pct']:.1f}% of supplier spend",
    delta_color="off",
    help="Total purchase order value flowing through sole-source suppliers.",
)
k6.metric(
    "P80 cost (MC)",
    fmt_usd(cost["p80"]),
    delta=fmt_usd(cost["p80"] - PROGRAM_BASELINE_USD),
    delta_color="normal",
    help="Monte Carlo P80 final cost -- the typical funding-reserve target.",
)

st.divider()

# ----------------------------------------------------------------------------
# Stoplight summary by workstream
# ----------------------------------------------------------------------------
st.subheader("Workstream Stoplight")
st.caption(
    "Green/amber/red driven by Cost Performance Index. Sorted by projected "
    "overrun (worst first)."
)

ws = load_workstream_summary()
display = ws[["workstream", "cpi", "spi", "eac", "vac"]].copy()
display["Health"] = display["cpi"].apply(
    lambda c: status_badge(c, good_if_above=0.95, neutral_if_above=0.90)
)
display["CPI"] = display["cpi"].round(3)
display["SPI"] = display["spi"].round(3)
display["EAC"] = display["eac"].apply(fmt_usd)
display["VAC"] = display["vac"].apply(fmt_usd)

st.dataframe(
    display[["Health", "workstream", "CPI", "SPI", "EAC", "VAC"]]
        .rename(columns={"workstream": "Workstream"}),
    hide_index=True,
    use_container_width=True,
)

st.divider()

# ----------------------------------------------------------------------------
# Where to go next -- explicit navigation cues
# ----------------------------------------------------------------------------
st.subheader("Where to dig in")

col_a, col_b = st.columns(2)
with col_a:
    st.markdown(
        """
        **Cost Performance** — workstream-level EVM, monthly burn-rate trends,
        and the top cost drivers.

        **Schedule Risk** — milestone slip, critical-path forecast finishes,
        and how upstream slip propagates into Integration & Test.

        **Supplier Risk** — composite supplier scorecard, sole-source exposure,
        and launch-impact suppliers.
        """
    )
with col_b:
    st.markdown(
        """
        **Risk Register** — open risks, heat map, and expected-value
        contribution to the program tail.

        **Mitigation Scenarios** — interactive before/after Monte Carlo with
        selectable mitigations; the "what should we do about it" page.
        """
    )

st.divider()
st.caption(
    "Synthetic data generated via `src/data_generator.py`. All analytics "
    "modules in `src/analytics/`. See `README.md` for project documentation."
)