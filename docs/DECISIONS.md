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

---

## Decision 002 — Latest closed month cutoff
**Date:** 2026-06-11
**Rule:** Model only uses data up to and including the latest fully closed month.
The current calendar month is always excluded even if data exists, because
partial-month retention numbers are misleading.
**Implementation:** `LATEST_CLOSED_MONTH` parameter at the top of every script.
Auto-detected by default. Can be manually overridden for backtesting.

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

---

## Decision 006 — Config file for stable model settings
**Date:** 2026-06-11
**Decision:** Stable model settings that change rarely (e.g. prediction horizon)
live in inputs/config.yml, not hardcoded in scripts.
**Rationale:** Separates code logic from model configuration. New owner can
adjust settings without touching Python code.
