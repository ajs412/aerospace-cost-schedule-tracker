"""
Data generator for the Aerospace Program Cost & Schedule Risk Tracker.

Builds a fully populated SQLite database at data/program.db that simulates a
24-month, $512M LEO satellite development program at the 16-month mark.

Design philosophy
-----------------
The generator is deterministic (fixed seed) so the resume story is reproducible.
Months 1-6 are deliberately healthy. From month 7 onward, four pathologies are
injected:
    1. Avionics supplier (Northbridge) starts slipping deliveries.
    2. Payload subcontractor (Helios Optics) overruns on focal-plane rework.
    3. Ground Software needs rework cycles after a failed integration test.
    4. Thermal redesign cascades into Integration & Test slip.

Target outputs at month 16:
    * Forecasted EAC ~$40M-$55M over baseline.
    * Launch Readiness Review forecast slip ~10-14 weeks.
    * Total open-risk exposure where proposed mitigations can buy back 35-50%.

Run:
    python -m src.data_generator
or:
    python src/data_generator.py
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import (
    DB_PATH,
    SCHEMA_PATH,
    RANDOM_SEED,
    PROGRAM_BASELINE_USD,
    PROGRAM_START,
    PROGRAM_DURATION_MONTHS,
    DATA_AS_OF_MONTH,
    WORKSTREAM_BUDGET_SHARE,
    PATHOLOGY_START_MONTH,
    COST_OVERRUN_MULTIPLIERS,
    SCHEDULE_PERFORMANCE,
    fmt_usd,
)

# A single RNG used throughout. Seeded for reproducibility.
RNG = np.random.default_rng(RANDOM_SEED)


# ============================================================================
# Helpers
# ============================================================================
def month_start(d: date) -> date:
    """Return the first day of the month for the given date."""
    return date(d.year, d.month, 1)


def add_months(d: date, n: int) -> date:
    """Add n calendar months to d, clamping the day to month-end if needed."""
    month_index = d.month - 1 + n
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    # Day-of-month clamp not needed here since we only call this on month-firsts.
    return date(year, month, min(d.day, 28))


def program_months() -> list[date]:
    """Return the first-of-month date for every month in the baseline."""
    return [add_months(PROGRAM_START, i) for i in range(PROGRAM_DURATION_MONTHS)]


def beta_pert_curve(n: int) -> np.ndarray:
    """
    S-curve weights for spreading a budget over n months. Programs typically
    spend slowly early, ramp through the middle, and taper at the end.
    Implemented as a discretized beta(2, 2) PDF, normalized to sum to 1.
    """
    x = np.linspace(0.02, 0.98, n)
    weights = x * (1 - x)  # beta(2, 2) up to a constant
    return weights / weights.sum()


# ============================================================================
# Table builders
# ============================================================================
def build_workstreams() -> pd.DataFrame:
    """Eight workstreams. End dates roughly track when each WS finishes its work."""
    leads = {
        "Payload":            "A. Reyes",
        "Avionics":           "M. Chen",
        "Propulsion":         "J. Okafor",
        "Ground Software":    "P. Iyer",
        "Thermal":            "S. Nakamura",
        "Structures":         "L. Petrov",
        "Integration & Test": "R. Alvarez",
        "Launch Readiness":   "K. Bauer",
    }
    # Each WS has a relative end month (out of 24) -- payload and propulsion
    # finish earlier, I&T and Launch Readiness anchor the end of the program.
    end_offsets = {
        "Payload":            18,
        "Avionics":           17,
        "Propulsion":         16,
        "Ground Software":    20,
        "Thermal":            18,
        "Structures":         15,
        "Integration & Test": 22,
        "Launch Readiness":   24,
    }

    rows = []
    for i, (ws_name, share) in enumerate(WORKSTREAM_BUDGET_SHARE.items(), start=1):
        rows.append({
            "workstream_id": f"WS{i:02d}",
            "name": ws_name,
            "lead": leads[ws_name],
            "baseline_budget_usd": round(PROGRAM_BASELINE_USD * share, 2),
            "baseline_start": PROGRAM_START.isoformat(),
            "baseline_end": add_months(PROGRAM_START, end_offsets[ws_name] - 1).isoformat(),
        })
    return pd.DataFrame(rows)


def build_suppliers() -> pd.DataFrame:
    """A small but realistic supplier base. Criticality drives risk weighting."""
    data = [
        # supplier_id, name,                    country, tier, criticality, sole_source
        ("SUP001", "Helios Optics",             "US", 1, "Critical", 1),
        ("SUP002", "Northbridge Avionics",      "US", 1, "Critical", 0),
        ("SUP003", "Meridian Propellants",      "DE", 2, "High",     1),
        ("SUP004", "Cascade Thermal Systems",   "US", 2, "High",     0),
        ("SUP005", "Polaris Structures",        "US", 2, "Medium",   0),
        ("SUP006", "Kestrel Ground Systems",    "UK", 2, "High",     0),
        ("SUP007", "Vector Launch Services",    "US", 1, "Critical", 1),
        ("SUP008", "Aurora Composites",         "CA", 3, "Medium",   0),
        ("SUP009", "Ridgeline Electronics",     "US", 3, "Medium",   0),
        ("SUP010", "Beacon Test Equipment",     "US", 3, "Low",      0),
    ]
    return pd.DataFrame(data, columns=[
        "supplier_id", "name", "country", "tier",
        "criticality", "sole_source_flag",
    ])


def build_milestones(ws_df: pd.DataFrame) -> pd.DataFrame:
    """
    Major program milestones. Forecast dates reflect the program-management
    view at month 16: items already complete keep their actual dates; future
    items are projected with slip baked in for the pathological workstreams.
    """
    as_of = add_months(PROGRAM_START, DATA_AS_OF_MONTH)
    ws_by_name = dict(zip(ws_df["name"], ws_df["workstream_id"]))

    # baseline_month_offset, forecast_slip_days
    milestones_spec = [
        ("M01", "System Requirements Review (SRR)",   None,                  3,  0),
        ("M02", "Preliminary Design Review (PDR)",    None,                  6,  0),
        ("M03", "Payload Critical Design Review",     "Payload",             9, 21),
        ("M04", "Avionics Critical Design Review",    "Avionics",            9, 28),
        ("M05", "Propulsion Qualification Review",    "Propulsion",         12, 14),
        ("M06", "Thermal Vacuum Test Readiness",      "Thermal",            14, 35),
        ("M07", "Ground Software v1.0 Release",       "Ground Software",    13, 42),
        ("M08", "Structures Proto-Flight Complete",   "Structures",         13,  7),
        ("M09", "Spacecraft Integration Complete",    "Integration & Test", 18, 56),
        ("M10", "Test Readiness Review (TRR)",        "Integration & Test", 20, 63),
        ("M11", "Flight Readiness Review (FRR)",      "Launch Readiness",   22, 77),
        ("M12", "Launch Readiness Review (LRR)",      "Launch Readiness",   24, 84),
    ]

    rows = []
    for mid, name, ws_name, offset, slip_days in milestones_spec:
        baseline = add_months(PROGRAM_START, offset - 1)
        forecast = baseline + timedelta(days=slip_days)

        if baseline <= as_of and slip_days == 0:
            status = "Complete"
            actual = baseline.isoformat()
        elif baseline <= as_of:
            # Slipped past baseline but may or may not have completed by as_of
            if forecast <= as_of:
                status = "Complete"
                actual = forecast.isoformat()
            else:
                status = "Slipped"
                actual = None
        elif slip_days > 14:
            status = "At Risk"
            actual = None
        else:
            status = "On Track"
            actual = None

        rows.append({
            "milestone_id": mid,
            "name": name,
            "workstream_id": ws_by_name.get(ws_name) if ws_name else None,
            "baseline_date": baseline.isoformat(),
            "forecast_date": forecast.isoformat(),
            "actual_date": actual,
            "status": status,
        })
    return pd.DataFrame(rows)


def build_tasks(ws_df: pd.DataFrame) -> pd.DataFrame:
    """
    Generate roughly 15-25 tasks per workstream. Tasks within a workstream
    chain (each predecessor is the prior task) to keep dependency logic
    realistic without over-engineering.
    """
    tasks = []
    task_counter = 1
    as_of = add_months(PROGRAM_START, DATA_AS_OF_MONTH)

    for _, ws in ws_df.iterrows():
        ws_id = ws["workstream_id"]
        ws_name = ws["name"]
        ws_budget = ws["baseline_budget_usd"]
        ws_start = date.fromisoformat(ws["baseline_start"])
        ws_end = date.fromisoformat(ws["baseline_end"])
        ws_days = (ws_end - ws_start).days

        n_tasks = int(RNG.integers(15, 26))
        # Random weights so task costs vary realistically.
        weights = RNG.dirichlet(np.ones(n_tasks) * 2.0)
        task_costs = weights * ws_budget

        # Tasks span the workstream window, staggered.
        starts = np.linspace(0, ws_days * 0.85, n_tasks).astype(int)
        durations = RNG.integers(20, 90, size=n_tasks)

        prev_id = None
        for j in range(n_tasks):
            task_id = f"T{task_counter:04d}"
            task_counter += 1
            b_start = ws_start + timedelta(days=int(starts[j]))
            b_end = b_start + timedelta(days=int(durations[j]))

            # Actuals: schedule slip injected for pathological workstreams from M7.
            if b_start <= as_of:
                schedule_slip_factor = 1.0
                if (b_start >= add_months(PROGRAM_START, PATHOLOGY_START_MONTH - 1)
                        and ws_name in SCHEDULE_PERFORMANCE):
                    spi = SCHEDULE_PERFORMANCE[ws_name]
                    schedule_slip_factor = 1.0 / spi  # SPI<1 -> tasks take longer
                actual_duration = int(durations[j] * schedule_slip_factor)
                a_start = b_start
                a_end_candidate = a_start + timedelta(days=actual_duration)

                if a_end_candidate <= as_of:
                    a_end = a_end_candidate.isoformat()
                    pct = 1.0
                else:
                    a_end = None
                    # In-progress task: percent complete proportional to elapsed.
                    elapsed = (as_of - a_start).days
                    pct = max(0.05, min(0.95, elapsed / max(actual_duration, 1)))
                a_start_str = a_start.isoformat()
            else:
                a_start_str = None
                a_end = None
                pct = 0.0

            tasks.append({
                "task_id": task_id,
                "workstream_id": ws_id,
                "name": f"{ws_name} task {j+1:02d}",
                "predecessor_ids": prev_id,
                "baseline_start": b_start.isoformat(),
                "baseline_end": b_end.isoformat(),
                "actual_start": a_start_str,
                "actual_end": a_end,
                "baseline_cost_usd": round(float(task_costs[j]), 2),
                "percent_complete": round(float(pct), 3),
            })
            prev_id = task_id

    return pd.DataFrame(tasks)


def build_monthly_budget(ws_df: pd.DataFrame) -> pd.DataFrame:
    """
    Time-phased planned value (PV) for each workstream across the full 24
    months, using an S-curve so spend ramps and tapers naturally.
    """
    months = program_months()
    rows = []
    for _, ws in ws_df.iterrows():
        ws_id = ws["workstream_id"]
        ws_budget = ws["baseline_budget_usd"]
        ws_start = date.fromisoformat(ws["baseline_start"])
        ws_end = date.fromisoformat(ws["baseline_end"])

        # Number of months this WS is active.
        active_months = [
            m for m in months if month_start(ws_start) <= m <= month_start(ws_end)
        ]
        if not active_months:
            continue
        curve = beta_pert_curve(len(active_months))
        active_set = set(active_months)

        idx = 0
        for m in months:
            if m in active_set:
                pv = ws_budget * curve[idx]
                idx += 1
            else:
                pv = 0.0
            rows.append({
                "period": m.isoformat(),
                "workstream_id": ws_id,
                "planned_value_usd": round(pv, 2),
            })
    return pd.DataFrame(rows)


def build_actual_spend(ws_df: pd.DataFrame, budget_df: pd.DataFrame) -> pd.DataFrame:
    """
    Earned value (EV) and actual cost (AC) for months 1..DATA_AS_OF_MONTH.

    Logic:
      * Months 1..6 (healthy): EV ~ PV, AC ~ PV * small noise (CPI near 1.0).
      * Months 7..16 (pathological): for each workstream apply
            EV = PV * SCHEDULE_PERFORMANCE[ws]      (SPI < 1)
            AC = EV * COST_OVERRUN_MULTIPLIERS[ws]  (CPI = 1 / multiplier)
        plus small Gaussian noise so the data does not look synthetic.
    """
    months = program_months()
    as_of_months = months[:DATA_AS_OF_MONTH]
    ws_by_id = dict(zip(ws_df["workstream_id"], ws_df["name"]))

    rows = []
    for _, row in budget_df.iterrows():
        period = date.fromisoformat(row["period"])
        if period not in as_of_months:
            continue
        ws_id = row["workstream_id"]
        ws_name = ws_by_id[ws_id]
        pv = row["planned_value_usd"]
        if pv <= 0:
            continue

        month_idx = months.index(period) + 1  # 1-based

        if month_idx < PATHOLOGY_START_MONTH:
            # Healthy phase: minor noise around plan.
            ev = pv * RNG.normal(0.99, 0.02)
            ac = pv * RNG.normal(1.01, 0.03)
        else:
            spi = SCHEDULE_PERFORMANCE.get(ws_name, 1.0)
            cost_mult = COST_OVERRUN_MULTIPLIERS.get(ws_name, 1.0)
            ev = pv * spi * RNG.normal(1.0, 0.03)
            ac = ev * cost_mult * RNG.normal(1.0, 0.03)

        # Committed cost: open POs not yet invoiced -- modeled as ~8% of AC.
        committed = ac * RNG.uniform(0.04, 0.12)

        rows.append({
            "period": row["period"],
            "workstream_id": ws_id,
            "earned_value_usd": round(max(ev, 0.0), 2),
            "actual_cost_usd": round(max(ac, 0.0), 2),
            "committed_cost_usd": round(max(committed, 0.0), 2),
        })
    return pd.DataFrame(rows)


def build_purchase_orders(ws_df: pd.DataFrame, supplier_df: pd.DataFrame) -> pd.DataFrame:
    """
    Roughly 50-70 purchase orders. The pathological suppliers (Helios,
    Northbridge, Cascade Thermal, Kestrel Ground) get late deliveries and
    occasional conditional/rejected quality from M7 onward.
    """
    # Mapping of supplier -> primary workstream they serve.
    supplier_ws = {
        "SUP001": "Payload",
        "SUP002": "Avionics",
        "SUP003": "Propulsion",
        "SUP004": "Thermal",
        "SUP005": "Structures",
        "SUP006": "Ground Software",
        "SUP007": "Launch Readiness",
        "SUP008": "Structures",
        "SUP009": "Avionics",
        "SUP010": "Integration & Test",
    }
    ws_by_name = dict(zip(ws_df["name"], ws_df["workstream_id"]))
    ws_budget_by_name = dict(zip(ws_df["name"], ws_df["baseline_budget_usd"]))

    # Suppliers that misbehave starting at M7.
    troubled = {"SUP001", "SUP002", "SUP004", "SUP006"}
    as_of = add_months(PROGRAM_START, DATA_AS_OF_MONTH)

    rows = []
    po_counter = 1
    for sup_id, ws_name in supplier_ws.items():
        # 4-9 POs per supplier.
        n_pos = int(RNG.integers(4, 10))
        # Spread the supplier's total spend across POs.
        # Roughly 25-45% of the WS budget flows through suppliers.
        sup_share = RNG.uniform(0.06, 0.18) * ws_budget_by_name[ws_name]
        po_values = RNG.dirichlet(np.ones(n_pos) * 1.5) * sup_share

        # PO dates spread across months 1..14 (POs issued ahead of need).
        po_months = sorted(RNG.integers(1, 15, size=n_pos))

        for i in range(n_pos):
            po_id = f"PO{po_counter:05d}"
            po_counter += 1
            po_date = add_months(PROGRAM_START, int(po_months[i]) - 1) + timedelta(
                days=int(RNG.integers(0, 28))
            )
            # Promised lead time 60-150 days.
            lead = int(RNG.integers(60, 150))
            promised = po_date + timedelta(days=lead)

            # Actual delivery and quality
            is_troubled_period = po_date >= add_months(
                PROGRAM_START, PATHOLOGY_START_MONTH - 1
            )
            if promised <= as_of:
                if sup_id in troubled and is_troubled_period:
                    delay = int(RNG.integers(20, 75))
                    quality = RNG.choice(
                        ["Accepted", "Conditional", "Rejected"], p=[0.55, 0.35, 0.10]
                    )
                else:
                    delay = int(RNG.integers(-5, 12))  # mostly on-time
                    quality = RNG.choice(
                        ["Accepted", "Conditional"], p=[0.92, 0.08]
                    )
                actual_delivery = (promised + timedelta(days=delay)).isoformat()
                if date.fromisoformat(actual_delivery) > as_of:
                    actual_delivery = None
                    quality = "Pending"
            else:
                actual_delivery = None
                quality = "Pending"

            rows.append({
                "po_id": po_id,
                "supplier_id": sup_id,
                "workstream_id": ws_by_name[ws_name],
                "po_value_usd": round(float(po_values[i]), 2),
                "po_date": po_date.isoformat(),
                "promised_delivery": promised.isoformat(),
                "actual_delivery": actual_delivery,
                "quality_acceptance": quality,
            })
    return pd.DataFrame(rows)


def build_risk_register(ws_df: pd.DataFrame) -> pd.DataFrame:
    """
    A hand-curated risk register that mirrors the four pathologies plus a
    handful of generic program risks. Exposure values are sized so the total
    open exposure plus EAC overrun matches the resume-story targets.
    """
    ws_by_name = dict(zip(ws_df["name"], ws_df["workstream_id"]))
    today = add_months(PROGRAM_START, DATA_AS_OF_MONTH)

    # (risk_id, title, ws_name, probability, impact_cost_usd, impact_sched_days,
    #  status, owner, opened_offset_months)
    risks = [
        ("R001", "Avionics supplier delivery slip (Northbridge)",   "Avionics",
            0.70, 14_000_000, 35, "Open",       "M. Chen",     7),
        ("R002", "Payload focal-plane rework (Helios Optics)",      "Payload",
            0.65, 18_000_000, 28, "Mitigating", "A. Reyes",    8),
        ("R003", "Ground Software v1.0 rework cycles",              "Ground Software",
            0.60,  9_000_000, 42, "Open",       "P. Iyer",     9),
        ("R004", "Thermal redesign cascade into I&T",               "Thermal",
            0.55, 11_000_000, 35, "Open",       "S. Nakamura", 9),
        ("R005", "I&T schedule compression from upstream slip",     "Integration & Test",
            0.75, 12_500_000, 49, "Open",       "R. Alvarez", 10),
        ("R006", "Propellant single-source qualification risk",     "Propulsion",
            0.30,  8_500_000, 30, "Open",       "J. Okafor",   5),
        ("R007", "Launch vehicle manifest slip",                    "Launch Readiness",
            0.25,  6_000_000, 21, "Open",       "K. Bauer",   11),
        ("R008", "Structures composite cure variance",              "Structures",
            0.20,  3_500_000, 14, "Open",       "L. Petrov",   6),
        ("R009", "ITAR export-license delays on payload optics",    "Payload",
            0.35,  4_500_000, 21, "Open",       "A. Reyes",    8),
        ("R010", "Ground station network availability",             "Ground Software",
            0.20,  2_500_000,  7, "Open",       "P. Iyer",    12),
    ]

    rows = []
    for r in risks:
        rid, title, ws_name, prob, impact_cost, impact_days, status, owner, opened = r
        rows.append({
            "risk_id": rid,
            "title": title,
            "workstream_id": ws_by_name[ws_name],
            "probability": prob,
            "impact_cost_usd": float(impact_cost),
            "impact_schedule_days": int(impact_days),
            "exposure_usd": round(prob * impact_cost, 2),
            "status": status,
            "owner": owner,
            "opened_date": add_months(PROGRAM_START, opened - 1).isoformat(),
        })
    return pd.DataFrame(rows)


def build_mitigation_actions() -> pd.DataFrame:
    """
    Proposed and in-flight mitigations tied to the risk register. Sized so
    the sum of (expected_reduction * exposure) lands at 35-50% of total open
    exposure, which is the target stated in the build spec.
    """
    actions = [
        # mitigation_id, risk_id, description, cost_usd, reduction_pct, status, impl_offset
        ("MIT001", "R001", "Dual-source Avionics flight computer with Ridgeline",
            2_400_000, 0.55, "Approved",   None),
        ("MIT002", "R002", "Tiger team to insource focal-plane rework",
            3_800_000, 0.60, "In Progress", 15),
        ("MIT003", "R003", "Add 2 senior engineers + restructure GS sprints",
            1_200_000, 0.50, "Proposed",   None),
        ("MIT004", "R004", "Parallel thermal-vac with vibration to recover schedule",
              600_000, 0.45, "Approved",   None),
        ("MIT005", "R005", "Compress I&T via 2-shift operations during integration",
            2_100_000, 0.40, "Proposed",   None),
        ("MIT006", "R006", "Qualify second propellant source (Aurora)",
            1_800_000, 0.65, "Proposed",   None),
        ("MIT007", "R007", "Secure backup launch slot with Vector",
              900_000, 0.50, "Proposed",   None),
        ("MIT008", "R009", "Accelerate ITAR review with dedicated counsel",
              250_000, 0.55, "Implemented", 13),
    ]
    rows = []
    for mid, rid, desc, cost, red, status, impl_offset in actions:
        impl_date = (
            add_months(PROGRAM_START, impl_offset - 1).isoformat() if impl_offset else None
        )
        rows.append({
            "mitigation_id": mid,
            "risk_id": rid,
            "description": desc,
            "cost_usd": float(cost),
            "expected_risk_reduction_pct": red,
            "status": status,
            "implemented_date": impl_date,
        })
    return pd.DataFrame(rows)


# ============================================================================
# Validation
# ============================================================================
def validate(conn: sqlite3.Connection) -> None:
    """Run a handful of sanity checks against the populated database."""
    checks = []

    # 1. Total baseline equals program baseline.
    total_baseline = conn.execute(
        "SELECT SUM(baseline_budget_usd) FROM workstreams"
    ).fetchone()[0]
    checks.append((
        "Workstream baselines sum to program baseline",
        abs(total_baseline - PROGRAM_BASELINE_USD) < 1.0,
        f"sum={fmt_usd(total_baseline)}",
    ))

    # 2. Planned value sums to baseline (allow 0.5% slack from S-curve clipping).
    total_pv = conn.execute(
        "SELECT SUM(planned_value_usd) FROM monthly_budget"
    ).fetchone()[0]
    checks.append((
        "Time-phased PV reconciles to baseline",
        abs(total_pv - PROGRAM_BASELINE_USD) / PROGRAM_BASELINE_USD < 0.005,
        f"sum={fmt_usd(total_pv)}",
    ))

    # 3. No actuals beyond the as-of month.
    as_of_iso = add_months(PROGRAM_START, DATA_AS_OF_MONTH).isoformat()
    future_actuals = conn.execute(
        "SELECT COUNT(*) FROM actual_spend WHERE period >= ?", (as_of_iso,)
    ).fetchone()[0]
    checks.append((
        "No actuals beyond as-of date",
        future_actuals == 0,
        f"future_rows={future_actuals}",
    ))

    # 4. Exposure = probability * impact_cost for every risk.
    bad_exposure = conn.execute("""
        SELECT COUNT(*) FROM risk_register
        WHERE ABS(exposure_usd - probability * impact_cost_usd) > 1.0
    """).fetchone()[0]
    checks.append((
        "Risk exposure equals probability * impact",
        bad_exposure == 0,
        f"violations={bad_exposure}",
    ))

    # 5. Every mitigation references an existing risk.
    orphans = conn.execute("""
        SELECT COUNT(*) FROM mitigation_actions m
        LEFT JOIN risk_register r ON r.risk_id = m.risk_id
        WHERE r.risk_id IS NULL
    """).fetchone()[0]
    checks.append((
        "All mitigations reference valid risks",
        orphans == 0,
        f"orphans={orphans}",
    ))

    print("\nValidation checks")
    print("-" * 60)
    for name, ok, detail in checks:
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {name:<48} {detail}")
    if not all(ok for _, ok, _ in checks):
        raise RuntimeError("One or more validation checks failed.")


# ============================================================================
# Executive summary
# ============================================================================
def executive_summary(conn: sqlite3.Connection) -> None:
    """Print the headline numbers that the analytics layer will refine later."""
    as_of_iso = add_months(PROGRAM_START, DATA_AS_OF_MONTH).isoformat()

    # Program-level EVM at as-of date.
    evm = conn.execute("""
        SELECT
            SUM(b.planned_value_usd) AS pv,
            SUM(a.earned_value_usd)  AS ev,
            SUM(a.actual_cost_usd)   AS ac
        FROM monthly_budget b
        LEFT JOIN actual_spend a
          ON a.period = b.period AND a.workstream_id = b.workstream_id
        WHERE b.period < ?
    """, (as_of_iso,)).fetchone()
    pv, ev, ac = (evm[0] or 0.0, evm[1] or 0.0, evm[2] or 0.0)
    cpi = ev / ac if ac else 0.0
    spi = ev / pv if pv else 0.0
    bac = PROGRAM_BASELINE_USD
    eac = bac / cpi if cpi else 0.0
    vac = bac - eac

    # Open risk exposure and mitigation potential.
    open_exp = conn.execute("""
        SELECT SUM(exposure_usd) FROM risk_register
        WHERE status IN ('Open', 'Mitigating')
    """).fetchone()[0] or 0.0

    mit_potential = conn.execute("""
        SELECT SUM(r.exposure_usd * m.expected_risk_reduction_pct)
        FROM mitigation_actions m
        JOIN risk_register r ON r.risk_id = m.risk_id
        WHERE m.status IN ('Proposed', 'Approved', 'In Progress')
          AND r.status IN ('Open', 'Mitigating')
    """).fetchone()[0] or 0.0

    mit_cost = conn.execute("""
        SELECT SUM(cost_usd) FROM mitigation_actions
        WHERE status IN ('Proposed', 'Approved', 'In Progress')
    """).fetchone()[0] or 0.0

    # Launch Readiness Review slip in weeks.
    lrr = conn.execute("""
        SELECT baseline_date, forecast_date FROM milestones
        WHERE milestone_id = 'M12'
    """).fetchone()
    lrr_slip_days = (date.fromisoformat(lrr[1]) - date.fromisoformat(lrr[0])).days
    lrr_slip_weeks = lrr_slip_days / 7.0

    # Supplier health snapshot.
    sup_health = conn.execute("""
        SELECT
            ROUND(100.0 * AVG(CASE
                WHEN actual_delivery IS NOT NULL
                 AND julianday(actual_delivery) <= julianday(promised_delivery)
                THEN 1.0 ELSE 0.0 END), 1) AS otd_pct,
            COUNT(*) FILTER (WHERE quality_acceptance = 'Rejected') AS rejects
        FROM purchase_orders
        WHERE actual_delivery IS NOT NULL
    """).fetchone()

    print("\n" + "=" * 64)
    print(f"  EXECUTIVE SUMMARY -- as of month {DATA_AS_OF_MONTH} ({as_of_iso})")
    print("=" * 64)
    print(f"  Program:                    ORION-LEO-1")
    print(f"  Baseline (BAC):             {fmt_usd(bac)}")
    print(f"  Planned Value (PV):         {fmt_usd(pv)}")
    print(f"  Earned Value (EV):          {fmt_usd(ev)}")
    print(f"  Actual Cost (AC):           {fmt_usd(ac)}")
    print(f"  CPI:                        {cpi:.3f}")
    print(f"  SPI:                        {spi:.3f}")
    print(f"  Forecast EAC:               {fmt_usd(eac)}")
    print(f"  Forecast Overrun (VAC):     {fmt_usd(-vac)}   "
          f"(target window: $40M-$55M)")
    print(f"  LRR forecast slip:          {lrr_slip_weeks:.1f} weeks   "
          f"(target window: 10-14 weeks)")
    print(f"  Open risk exposure:         {fmt_usd(open_exp)}")
    print(f"  Mitigation potential:       {fmt_usd(mit_potential)}   "
          f"({100*mit_potential/open_exp:.1f}% of exposure)")
    print(f"  Mitigation cost:            {fmt_usd(mit_cost)}   "
          f"(ROI: {mit_potential/mit_cost:.1f}x)")
    print(f"  Supplier OTD rate:          {sup_health[0]}%")
    print(f"  Quality rejections:         {sup_health[1]}")
    print("=" * 64)


# ============================================================================
# Main
# ============================================================================
def main() -> None:
    print(f"Generating program database at {DB_PATH} ...")

    # Build all tables in memory first; only write to DB once everything is ready.
    ws_df         = build_workstreams()
    suppliers_df  = build_suppliers()
    milestones_df = build_milestones(ws_df)
    tasks_df      = build_tasks(ws_df)
    budget_df     = build_monthly_budget(ws_df)
    actuals_df    = build_actual_spend(ws_df, budget_df)
    pos_df        = build_purchase_orders(ws_df, suppliers_df)
    risks_df      = build_risk_register(ws_df)
    mitigations_df = build_mitigation_actions()

    # Recreate the database from scratch.
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")

    # Apply schema.
    schema_sql = SCHEMA_PATH.read_text()
    conn.executescript(schema_sql)

    # Write tables.
    write_order = [
        ("workstreams",        ws_df),
        ("suppliers",          suppliers_df),
        ("milestones",         milestones_df),
        ("tasks",              tasks_df),
        ("monthly_budget",     budget_df),
        ("actual_spend",       actuals_df),
        ("purchase_orders",    pos_df),
        ("risk_register",      risks_df),
        ("mitigation_actions", mitigations_df),
    ]
    print("\nLoaded row counts")
    print("-" * 60)
    for name, df in write_order:
        df.to_sql(name, conn, if_exists="append", index=False)
        print(f"  {name:<22} {len(df):>6} rows")

    conn.commit()

    validate(conn)
    executive_summary(conn)

    conn.close()
    print(f"\nDone. Database written to: {DB_PATH}")


if __name__ == "__main__":
    main()