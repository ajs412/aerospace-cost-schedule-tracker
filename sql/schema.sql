-- ============================================================================
-- Aerospace Program Cost & Schedule Risk Tracker
-- Schema for a simulated $512M LEO satellite development program
--
-- Design notes:
--   * SQLite-compatible, but uses only standard SQL types so it ports cleanly
--     to PostgreSQL / BigQuery later (no AUTOINCREMENT, no untyped columns).
--   * All monetary values in USD. All dates in ISO 8601 (YYYY-MM-DD).
--   * Foreign keys are declared and should be enforced at runtime via:
--         PRAGMA foreign_keys = ON;
-- ============================================================================

-- Drop in reverse dependency order so re-seeding is idempotent.
DROP TABLE IF EXISTS mitigation_actions;
DROP TABLE IF EXISTS risk_register;
DROP TABLE IF EXISTS purchase_orders;
DROP TABLE IF EXISTS actual_spend;
DROP TABLE IF EXISTS monthly_budget;
DROP TABLE IF EXISTS tasks;
DROP TABLE IF EXISTS milestones;
DROP TABLE IF EXISTS suppliers;
DROP TABLE IF EXISTS workstreams;

-- ----------------------------------------------------------------------------
-- workstreams: the 8 top-level program areas. The WBS root.
-- ----------------------------------------------------------------------------
CREATE TABLE workstreams (
    workstream_id        TEXT PRIMARY KEY,
    name                 TEXT NOT NULL UNIQUE,
    lead                 TEXT NOT NULL,
    baseline_budget_usd  REAL NOT NULL CHECK (baseline_budget_usd > 0),
    baseline_start       DATE NOT NULL,
    baseline_end         DATE NOT NULL,
    CHECK (baseline_end > baseline_start)
);

-- ----------------------------------------------------------------------------
-- suppliers: vendor base. Criticality and sole-source flags drive supplier
-- risk analytics later.
-- ----------------------------------------------------------------------------
CREATE TABLE suppliers (
    supplier_id          TEXT PRIMARY KEY,
    name                 TEXT NOT NULL UNIQUE,
    country              TEXT NOT NULL,
    tier                 INTEGER NOT NULL CHECK (tier IN (1, 2, 3)),
    criticality          TEXT NOT NULL CHECK (criticality IN ('Critical', 'High', 'Medium', 'Low')),
    sole_source_flag     INTEGER NOT NULL CHECK (sole_source_flag IN (0, 1))
);

-- ----------------------------------------------------------------------------
-- milestones: the executive-visible gates (PDR, CDR, TRR, FRR, LRR, etc.).
-- forecast_date is updated as the program progresses; actual_date is set on
-- completion.
-- ----------------------------------------------------------------------------
CREATE TABLE milestones (
    milestone_id         TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    workstream_id        TEXT,
    baseline_date        DATE NOT NULL,
    forecast_date        DATE NOT NULL,
    actual_date          DATE,
    status               TEXT NOT NULL CHECK (status IN ('Complete', 'On Track', 'At Risk', 'Slipped')),
    FOREIGN KEY (workstream_id) REFERENCES workstreams(workstream_id)
);

-- ----------------------------------------------------------------------------
-- tasks: the work breakdown structure. predecessor_ids is a comma-separated
-- list for v1 simplicity; can be normalized into a task_dependencies table
-- later if recursive CTE critical-path is desired.
-- ----------------------------------------------------------------------------
CREATE TABLE tasks (
    task_id              TEXT PRIMARY KEY,
    workstream_id        TEXT NOT NULL,
    name                 TEXT NOT NULL,
    predecessor_ids      TEXT,
    baseline_start       DATE NOT NULL,
    baseline_end         DATE NOT NULL,
    actual_start         DATE,
    actual_end           DATE,
    baseline_cost_usd    REAL NOT NULL CHECK (baseline_cost_usd >= 0),
    percent_complete     REAL NOT NULL DEFAULT 0.0 CHECK (percent_complete BETWEEN 0.0 AND 1.0),
    FOREIGN KEY (workstream_id) REFERENCES workstreams(workstream_id),
    CHECK (baseline_end >= baseline_start)
);

-- ----------------------------------------------------------------------------
-- monthly_budget: the time-phased baseline (Planned Value source).
-- One row per workstream per month for the full 24-month baseline.
-- ----------------------------------------------------------------------------
CREATE TABLE monthly_budget (
    period               DATE NOT NULL,           -- first-of-month
    workstream_id        TEXT NOT NULL,
    planned_value_usd    REAL NOT NULL CHECK (planned_value_usd >= 0),
    PRIMARY KEY (period, workstream_id),
    FOREIGN KEY (workstream_id) REFERENCES workstreams(workstream_id)
);

-- ----------------------------------------------------------------------------
-- actual_spend: the time-phased actuals (AC) and earned value (EV).
-- Populated only for months that have elapsed (months 1..16 in this scenario).
-- ----------------------------------------------------------------------------
CREATE TABLE actual_spend (
    period               DATE NOT NULL,
    workstream_id        TEXT NOT NULL,
    earned_value_usd     REAL NOT NULL CHECK (earned_value_usd >= 0),
    actual_cost_usd      REAL NOT NULL CHECK (actual_cost_usd >= 0),
    committed_cost_usd   REAL NOT NULL DEFAULT 0.0 CHECK (committed_cost_usd >= 0),
    PRIMARY KEY (period, workstream_id),
    FOREIGN KEY (workstream_id) REFERENCES workstreams(workstream_id)
);

-- ----------------------------------------------------------------------------
-- purchase_orders: supplier-level commitments. promised vs actual delivery
-- and quality acceptance feed supplier-health scoring.
-- ----------------------------------------------------------------------------
CREATE TABLE purchase_orders (
    po_id                TEXT PRIMARY KEY,
    supplier_id          TEXT NOT NULL,
    workstream_id        TEXT NOT NULL,
    po_value_usd         REAL NOT NULL CHECK (po_value_usd > 0),
    po_date              DATE NOT NULL,
    promised_delivery    DATE NOT NULL,
    actual_delivery      DATE,
    quality_acceptance   TEXT CHECK (quality_acceptance IN ('Accepted', 'Conditional', 'Rejected', 'Pending')),
    FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id),
    FOREIGN KEY (workstream_id) REFERENCES workstreams(workstream_id)
);

-- ----------------------------------------------------------------------------
-- risk_register: standard 5x5 risk scoring. exposure_usd = probability * impact.
-- ----------------------------------------------------------------------------
CREATE TABLE risk_register (
    risk_id                  TEXT PRIMARY KEY,
    title                    TEXT NOT NULL,
    workstream_id            TEXT NOT NULL,
    probability              REAL NOT NULL CHECK (probability BETWEEN 0.0 AND 1.0),
    impact_cost_usd          REAL NOT NULL CHECK (impact_cost_usd >= 0),
    impact_schedule_days     INTEGER NOT NULL CHECK (impact_schedule_days >= 0),
    exposure_usd             REAL NOT NULL CHECK (exposure_usd >= 0),
    status                   TEXT NOT NULL CHECK (status IN ('Open', 'Mitigating', 'Closed', 'Realized')),
    owner                    TEXT NOT NULL,
    opened_date              DATE NOT NULL,
    FOREIGN KEY (workstream_id) REFERENCES workstreams(workstream_id)
);

-- ----------------------------------------------------------------------------
-- mitigation_actions: actions proposed or implemented against risks.
-- expected_risk_reduction_pct is the fraction of exposure the action removes
-- if implemented successfully.
-- ----------------------------------------------------------------------------
CREATE TABLE mitigation_actions (
    mitigation_id                TEXT PRIMARY KEY,
    risk_id                      TEXT NOT NULL,
    description                  TEXT NOT NULL,
    cost_usd                     REAL NOT NULL CHECK (cost_usd >= 0),
    expected_risk_reduction_pct  REAL NOT NULL CHECK (expected_risk_reduction_pct BETWEEN 0.0 AND 1.0),
    status                       TEXT NOT NULL CHECK (status IN ('Proposed', 'Approved', 'In Progress', 'Implemented', 'Rejected')),
    implemented_date             DATE,
    FOREIGN KEY (risk_id) REFERENCES risk_register(risk_id)
);

-- ----------------------------------------------------------------------------
-- Indexes for the most common analytical access patterns.
-- ----------------------------------------------------------------------------
CREATE INDEX idx_tasks_workstream     ON tasks(workstream_id);
CREATE INDEX idx_budget_period        ON monthly_budget(period);
CREATE INDEX idx_actuals_period       ON actual_spend(period);
CREATE INDEX idx_po_supplier          ON purchase_orders(supplier_id);
CREATE INDEX idx_po_workstream        ON purchase_orders(workstream_id);
CREATE INDEX idx_risk_workstream      ON risk_register(workstream_id);
CREATE INDEX idx_risk_status          ON risk_register(status);
CREATE INDEX idx_mitigation_risk      ON mitigation_actions(risk_id);