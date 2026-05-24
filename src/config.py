"""
Central configuration for the Aerospace Program Cost & Schedule Risk Tracker.

All paths, constants, and program parameters live here so they can be imported
consistently from the data generator, analytics modules, and Streamlit pages.
"""

from pathlib import Path
from datetime import date

# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------
# PROJECT_ROOT resolves to the repository root regardless of where Python is
# invoked from, so notebooks, scripts, and the Streamlit app all agree.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

DATA_DIR: Path     = PROJECT_ROOT / "data"
RAW_DIR: Path      = DATA_DIR / "raw"
PROCESSED_DIR: Path = DATA_DIR / "processed"
SQL_DIR: Path      = PROJECT_ROOT / "sql"

DB_PATH: Path      = DATA_DIR / "program.db"
SCHEMA_PATH: Path  = SQL_DIR / "schema.sql"

# Create folders if missing. Safe to call repeatedly.
for _d in (DATA_DIR, RAW_DIR, PROCESSED_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ----------------------------------------------------------------------------
# Reproducibility
# ----------------------------------------------------------------------------
RANDOM_SEED: int = 42

# ----------------------------------------------------------------------------
# Program parameters
# ----------------------------------------------------------------------------
# A simulated LEO satellite development program. The numbers below are the
# baseline; the data generator deliberately injects pathologies on top of
# this baseline starting in month 7.
PROGRAM_NAME: str          = "ORION-LEO-1"
PROGRAM_BASELINE_USD: float = 512_000_000.0   # $512M baseline
PROGRAM_START: date        = date(2025, 1, 1)
PROGRAM_DURATION_MONTHS: int = 24             # 24-month development
DATA_AS_OF_MONTH: int      = 16               # we have 16 months of actuals

# Workstream allocation as a fraction of the baseline budget. Sums to 1.0.
# Weighting reflects a typical LEO smallsat program: payload and integration
# carry the most cost, ground software and thermal less.
WORKSTREAM_BUDGET_SHARE: dict[str, float] = {
    "Payload":              0.27,
    "Avionics":             0.14,
    "Propulsion":           0.13,
    "Ground Software":      0.08,
    "Thermal":              0.07,
    "Structures":           0.10,
    "Integration & Test":   0.15,
    "Launch Readiness":     0.06,
}
assert abs(sum(WORKSTREAM_BUDGET_SHARE.values()) - 1.0) < 1e-9, \
    "Workstream budget shares must sum to 1.0"

# ----------------------------------------------------------------------------
# Pathology toggles (the deliberate problems that the analytics will surface)
# ----------------------------------------------------------------------------
# These knobs shape the "before" picture so it matches the resume story:
#   * $40M-$55M forecasted overrun
#   * 10-14 weeks of launch slip
#   * mitigations that can buy back 35%-50% of the exposure
PATHOLOGY_START_MONTH: int = 7                 # problems begin appearing in M7

# Workstream-specific cost-performance multipliers from M7 onward.
# A value of 1.20 means the workstream burns 20% more cost than earned value
# (i.e. CPI = 1 / 1.20 = 0.83).
COST_OVERRUN_MULTIPLIERS: dict[str, float] = {
    "Payload":              1.22,  # subcontractor focal-plane rework
    "Avionics":             1.14,  # supplier-driven, less internal burn
    "Propulsion":           1.08,
    "Ground Software":      1.18,  # rework cycles
    "Thermal":              1.20,  # redesign
    "Structures":           1.03,
    "Integration & Test":   1.12,  # absorbing upstream slip
    "Launch Readiness":     1.02,
}

# Schedule performance (EV / PV) from M7 onward. <1.0 means behind schedule.
SCHEDULE_PERFORMANCE: dict[str, float] = {
    "Payload":              0.88,
    "Avionics":             0.82,  # supplier delays
    "Propulsion":           0.94,
    "Ground Software":      0.86,
    "Thermal":              0.85,
    "Structures":           0.97,
    "Integration & Test":   0.83,  # downstream of upstream slip
    "Launch Readiness":     0.95,
}

# ----------------------------------------------------------------------------
# Display helpers
# ----------------------------------------------------------------------------
def fmt_usd(x: float) -> str:
    """Format a dollar amount as '$123.4M' or '$1.2B'."""
    if abs(x) >= 1e9:
        return f"${x/1e9:,.2f}B"
    if abs(x) >= 1e6:
        return f"${x/1e6:,.1f}M"
    if abs(x) >= 1e3:
        return f"${x/1e3:,.1f}K"
    return f"${x:,.0f}"