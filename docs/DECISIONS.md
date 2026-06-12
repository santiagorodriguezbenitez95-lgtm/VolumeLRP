# Decisions Log

This file captures every non-obvious architectural or business logic decision made during the migration.
Purpose: anyone picking up this project can understand *why* things are built the way they are,
not just *what* they do.

---

## Decision 001 — PRIME_SUBSCRIPTION dimension excluded from retention model
**Date:** 2026-06-11
**Decision:** The PRIME_SUBSCRIPTION dimension (PRIME / NO PRIME) is aggregated away
before calculating retention. PRIME is used in later modules but not in the baseline
cohort retention model.
**Update (2026-06-11):** Same aggregation applies to the Frequency module — both
retention and frequency operate on the PRIME+NO PRIME aggregated USERS/ORDERS
columns produced in the shared load step.

---

## Decision 002 — Latest closed month cutoff
**Date:** 2026-06-11
**Rule:** Model only uses data up to and including the latest fully closed month.
The current calendar month is always excluded even if data exists, because
partial-month retention numbers are misleading.
**Implementation:** see Decision 007 — auto-detected from data.

---

## Decision 003 — LATEST_CLOSED_MONTH enables backtesting
**Date:** 2026-06-11
**Insight:** By overriding LATEST_CLOSED_MONTH to a historical date and feeding
actual lever values for the intervening period, the model can be run as if it
were that date in the past. This produces an objective backtest:
model predictions vs. observed reality.
**UI implication:** Streamlit/Rappidata should expose this as a user-selectable
parameter, defaulting to auto-detected value.

---

## Decision 004 — Data source for Phase 0-1: Excel file
**Date:** 2026-06-11
**Decision:** During early phases, cohort data is loaded from a local Excel file
(`data/cohorts.xlsx`) rather than a live Snowflake connection. This is because
the build is happening on a personal GitHub account without corporate Snowflake
access. The `pipeline/snowflake_client.py` module will replace this in a later
phase when enterprise access is set up.

---

## Decision 005 — Prediction horizon
**Date:** 2026-06-11
**Decision:** The model predicts retention through December 2030, matching
the seasonality factors file coverage.
**Rationale:** Planning cycles require long-horizon projections. December 2030
is the current planning horizon agreed with the business.
**Implication:** If the planning horizon extends beyond 2030, the seasonality
factors file must be updated first. The model will warn the user if a required
month is missing from the seasonality table.
**Update (2026-06-11):** Frequency predictions use the same horizon and the
same seasonality coverage validation (shared via pipeline/shared.py).

---

## Decision 006 — Config file for stable model settings
**Date:** 2026-06-11
**Decision:** Stable model settings that change rarely (e.g. prediction horizon)
live in inputs/config.yml, not hardcoded in scripts.
**Rationale:** Separates code logic from model configuration. New owner can
adjust settings without touching Python code.

---

## Decision 007 — LATEST_CLOSED_MONTH derived from data, not system clock
**Date:** 2026-06-11
**Decision:** LATEST_CLOSED_MONTH = max COHORT_MONTH where LT=0 in the data.
**Rationale:** The system clock of whatever machine runs this script is
unreliable for business logic. The data itself tells us which month closed
last — a new cohort (LT=0 row) only exists once that month is complete.

---

## Decision 008 — Cohort activity gaps are not excluded
**Date:** 2026-06-11
**Decision:** Cohorts whose latest observed LT_MONTH is before
LATEST_CLOSED_MONTH are NOT excluded from projection. They are projected
forward from their last known point.
**Rationale:** Especially in small countries (CR, UY, etc.), a cohort can
have zero activity for a period and later resume if a user reactivates.
This is normal behavior, not a data quality issue. Confirmed by model owner.
**Edge case:** Cohorts with no activity since before the seasonality table's
coverage start (2021-02) will simply produce zero predicted rows — handled
gracefully by project_cohort's early-exit logic.

---

## Decision 009 — Shared logic generalized into pipeline/shared.py
**Date:** 2026-06-11
**Decision:** Logic shared between Retention and Frequency (load/aggregate
cohorts, load/validate seasonality, weighted averages, prediction factor
calculation, forward projection) was extracted from
exploration/retention_exploration.py into generic, parameterized functions
in pipeline/shared.py, following the DRY principle (Don't Repeat Yourself).
**Key generalized functions:**
- `load_and_aggregate_cohorts()`, `load_and_validate_seasonality()` —
  identical for both modules, no parameters needed
- `weighted_average(df, value_col, weight_col, group_cols)` — generic
  weighted-average helper
- `calculate_prediction_factors(df, metric_col, weight_col, ...)` —
  parameterized by which metric and which weight column to use
- `project_metric(..., metric_col, cap_at_one, extra_cols)` — generalized
  version of project_cohort; cap_at_one toggles the retention-specific
  1.0 cap (off for frequency); extra_cols allows passing through
  module-specific columns (e.g. M0_USERS for retention)
**Rationale:** Avoids duplicating the prediction-factor and projection logic
across modules — a future bug fix or change to this logic only needs to
happen once.
**Validation:** models/baseline/retention.py (using shared.py) must produce
identical output to exploration/retention_exploration.py before this
refactor is considered validated.

---

## Decision 010 — Frequency calculation, prediction factor weighting, and cap
**Date:** 2026-06-11
**Decision:**
- Historical FREQUENCY(LT) = ORDERS(LT) / USERS(LT) — a simple row-wise
  ratio, unlike retention which references a fixed M0 row.
- Frequency's prediction factor is a weighted average of
  frequency(LT+1)/frequency(LT), weighted by USERS(LT) — the actual active
  users at that LT transition — NOT M0_USERS (which retention uses).
- Frequency has NO upper cap (unlike retention's 1.0 cap, since frequency
  is not a percentage of a fixed reference).
**Rationale:** Frequency naturally varies with cohort size at each point in
time, not cohort size at acquisition, so weighting by current USERS is more
representative of the transition being averaged. No natural ceiling exists
for orders-per-user, so no cap is applied (sanity bounds, if any, deferred
to the validation module).

---

## Decision 011 — New cohort frequency seeding
**Date:** 2026-06-11
**Decision:** For new (not-yet-existing) cohorts, LT=0 FREQUENCY is seeded as
follows:
- For the FIRST predicted cohort month (LATEST_CLOSED_MONTH + 1): seed =
  weighted average (by USERS) of LT=0 FREQUENCY across the last 12 cohort
  months — a smoothed starting point, recalculated dynamically as
  LATEST_CLOSED_MONTH advances (never hardcoded to a specific month).
- For every subsequent new cohort month: LT=0 FREQUENCY = previous new
  cohort's LT=0 FREQUENCY x seasonality(this month) — chained forward,
  mirroring the existing "new users" chaining pattern
  (new_users(next) = new_users(current) x seasonality(next)).
From LT=0, each new cohort's frequency is then projected forward via the
same project_metric logic as any other cohort (no special-casing beyond
the LT=0 seed).
**Rationale:** A single month's LT=0 frequency can be noisy; a 12-month
weighted average gives a more stable starting point. After that, seasonality
alone drives month-to-month variation, consistent with how new user volume
is projected.

---

## Decision 012 — Combine step: cohort-level USERS/ORDERS and aggregate FREQUENCY
**Date:** 2026-06-11
**Decision:** models/baseline/combine.py merges retention_full.csv and
frequency_full.csv on (COUNTRY, COHORT_MONTH, LT, LT_MONTH) via inner join,
then:
- USERS(LT) = RETENTION(LT) x M0_USERS
- ORDERS(LT) = FREQUENCY(LT) x USERS(LT)
- Monthly totals: TOTAL_USERS and TOTAL_ORDERS are SUMS of cohort-level
  USERS/ORDERS across all cohorts active in that month.
- Aggregate monthly FREQUENCY = TOTAL_ORDERS / TOTAL_USERS — recalculated
  at the aggregate level, NOT an average of cohort-level FREQUENCY values.
**Rationale:** Averaging cohort-level frequencies directly would weight every
cohort equally regardless of size. Deriving the aggregate from summed totals
produces the correct user-weighted result.
**Note:** retention.py's previous standalone monthly totals output
(data/monthly_totals.csv, TOTAL_USERS only) was removed — combine.py is now
the single source of monthly totals for both users and orders.

---

## Decision 013 — FX / local currency conversion (PENDING — to be finalized once implemented)
**Date:** 2026-06-11 (proposed)
**Status:** Plan agreed, not yet implemented.
**Problem:** Raw GMV (and therefore AOV = GMV/ORDERS) is in USD. This mixes
real local-currency AOV trends with FX rate fluctuations, which would
contaminate AOV prediction factors and seasonality effects if used as-is.
**Decision:** Build an isolated `pipeline/fx_conversion.py` module that
converts GMV to local currency using historical FX rates (held by model
owner), applied ONLY to AOV/GMV calculations. Retention and Frequency are
USERS/ORDERS-based and are NOT affected by currency — no changes needed
to those modules.
**Rationale for doing this now vs. waiting for BI:** The long-term correct
fix is for BI to provide GMV in local currency at the source, but this
depends on a team with an unknown timeline, and would block AOV work
given the handoff deadline (2026-07-31). A self-contained, clearly-documented
temporary module can be removed/bypassed later with minimal impact, since
it sits as an isolated layer between raw data and AOV calculations.
**To do when implemented:** confirm FX data format/source, confirm exact
point in the pipeline where conversion is applied, update this entry with
implementation details and validation results.