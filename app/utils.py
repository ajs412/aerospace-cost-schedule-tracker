"""
Shared helpers for the Streamlit dashboard.

Centralizes:
    * Cached data loads so each page doesn't re-query SQLite on every rerun
    * A small set of formatting helpers used across pages
    * Plotly theme defaults so all charts look consistent

The @st.cache_data decorator memoizes returned DataFrames; Streamlit
invalidates the cache when source code or arguments change. For a local app
backed by SQLite this is more than fast enough.
"""

from __future__ import annotations

import streamlit as st
import plotly.graph_objects as go

from src.config import fmt_usd, DATA_AS_OF_MONTH, PROGRAM_START
from src.analytics import evm, schedule, supplier, monte_carlo, mitigation
from datetime import date


# ----------------------------------------------------------------------------
# Cached data loaders
# ----------------------------------------------------------------------------
# All loaders return plain pandas DataFrames so Streamlit can hash + cache them.
# TTL is set generously (1 hour) because the underlying SQLite file is static
# until the user reruns the data generator.

@st.cache_data(ttl=3600)
def load_program_summary():
    return evm.program_summary()


@st.cache_data(ttl=3600)
def load_workstream_summary():
    return evm.workstream_summary()


@st.cache_data(ttl=3600)
def load_monthly_trends():
    return evm.monthly_trends()


@st.cache_data(ttl=3600)
def load_milestone_slip():
    return schedule.milestone_slip()


@st.cache_data(ttl=3600)
def load_workstream_avg_slip():
    return schedule.workstream_avg_slip()


@st.cache_data(ttl=3600)
def load_critical_path(top_n: int = 10):
    return schedule.critical_path_tasks(top_n=top_n)


@st.cache_data(ttl=3600)
def load_lrr_forecast():
    return schedule.launch_readiness_forecast()


@st.cache_data(ttl=3600)
def load_propagation():
    return schedule.upstream_to_it_propagation()


@st.cache_data(ttl=3600)
def load_supplier_scorecard():
    return supplier.supplier_scorecard()


@st.cache_data(ttl=3600)
def load_launch_suppliers():
    return supplier.launch_risk_suppliers()


@st.cache_data(ttl=3600)
def load_concentration():
    return supplier.concentration_summary()


@st.cache_data(ttl=3600)
def load_risk_register():
    """Load the full open risk register with workstream names."""
    import pandas as pd
    from src.db import get_conn
    with get_conn() as conn:
        return pd.read_sql_query("""
            SELECT
                r.risk_id, r.title, w.name AS workstream,
                r.probability, r.impact_cost_usd, r.impact_schedule_days,
                r.exposure_usd, r.status, r.owner, r.opened_date
            FROM risk_register r
            JOIN workstreams w ON w.workstream_id = r.workstream_id
            ORDER BY r.exposure_usd DESC
        """, conn)


@st.cache_data(ttl=3600)
def load_mitigation_catalog():
    return mitigation.mitigation_catalog()


# Monte Carlo runs are slightly more expensive (~1 second for 10K iter) so we
# cache more aggressively. The cache key includes the mitigation tuple so
# different scenarios are independently memoized.
@st.cache_data(ttl=3600)
def run_cost_distribution(mitigation_ids: tuple = ()):
    mit_map = mitigation._mitigation_map(list(mitigation_ids))
    return monte_carlo.simulate_cost(mitigations=mit_map)


@st.cache_data(ttl=3600)
def run_schedule_distribution(mitigation_ids: tuple = ()):
    mit_map = mitigation._mitigation_map(list(mitigation_ids))
    return monte_carlo.simulate_schedule(mitigations=mit_map)


@st.cache_data(ttl=3600)
def load_cost_summary():
    return monte_carlo.cost_distribution_summary()


@st.cache_data(ttl=3600)
def load_schedule_summary():
    return monte_carlo.schedule_distribution_summary()


@st.cache_data(ttl=3600)
def load_risk_contributions():
    return monte_carlo.risk_contribution_table()


# ----------------------------------------------------------------------------
# Formatting helpers
# ----------------------------------------------------------------------------
def as_of_date_iso() -> str:
    """ISO string for the data-as-of date (matches what evm.py uses)."""
    month_index = PROGRAM_START.month - 1 + DATA_AS_OF_MONTH
    return date(
        PROGRAM_START.year + month_index // 12,
        month_index % 12 + 1,
        1,
    ).isoformat()


def page_header(title: str, subtitle: str | None = None) -> None:
    """Standard header used on every page."""
    st.title(title)
    if subtitle:
        st.caption(subtitle)
    st.caption(f"Program: ORION-LEO-1  |  Data as of: {as_of_date_iso()}")
    st.divider()


def status_badge(value: float, good_if_above: float, neutral_if_above: float) -> str:
    """
    Return a colored emoji indicator for a metric. Used for CPI/SPI/OTD-style
    metrics where higher is better.
    """
    if value >= good_if_above:
        return "🟢"
    if value >= neutral_if_above:
        return "🟡"
    return "🔴"


# ----------------------------------------------------------------------------
# Plotly theme
# ----------------------------------------------------------------------------
# A muted, executive-style palette. Avoids the saturated default Plotly colors
# that scream "first time using a charting library."
COLORS = {
    "primary":   "#1f4e79",   # deep navy
    "secondary": "#7fa8d6",   # muted blue
    "good":      "#2e7d32",   # green
    "warn":      "#ed6c02",   # amber
    "bad":       "#c62828",   # red
    "neutral":   "#6b7280",   # gray
    "fill_band": "rgba(31, 78, 121, 0.15)",
}


def apply_layout(fig: go.Figure, height: int = 400) -> go.Figure:
    """Apply the standard layout: tight margins, no clutter, executive look."""
    fig.update_layout(
        height=height,
        margin=dict(l=40, r=20, t=40, b=40),
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(family="sans-serif", size=12, color="#1f2937"),
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right",  x=1.0,
        ),
        xaxis=dict(showgrid=True, gridcolor="#e5e7eb"),
        yaxis=dict(showgrid=True, gridcolor="#e5e7eb"),
    )
    return fig