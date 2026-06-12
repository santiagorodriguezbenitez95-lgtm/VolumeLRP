# Project Context

This is the primary context document for the Growth Volume Model migration project.
Paste this file into any new Claude conversation or Project to restore full context.
Last updated: 2026-06-11

---

## What this project is

Migration of the Growth Volume Model from Google Sheets to a Python + Snowflake +
Streamlit/Rappidata platform. The model is the core analytical tool used to define
growth strategy.

**Owner:** Santiago Rodriguez (leaving 2026-07-31 — this project is the handoff artifact)
**Stack:** Python, Snowflake SQL, Streamlit or Rappidata (TBD — see Decision/architecture)
**Repo:** github.com/santiagorodriguezbenitez95-lgtm/VolumeLRP
**Working environment:** GitHub Codespaces (Python 3.12, browser-based VS Code)

---

## Model structure

Four interdependent modules:

1. **Baseline (CC&S)** — cohort-based model projecting GMV, Orders, Users
   via AOV x frequency x retention logic. Currently in active development.
   - User Retention: DONE (see below)
   - AOV: not started
   - Frequency: not started
   - Combine into GMV/Orders/Users: not started
2. **Micro-models (~30)** — each estimates volume and cost impact of improving
   a specific growth lever. Mix of structurally similar and heterogeneous models.
   Not started.
3. **Cannibalization** — estimates overlap between levers. Not started.
4. **Zone-level Appendix** — translates country-level outputs to zone-level targets.
   Not started.

---

## Current status

**Phase 0 — Foundations (Jun 11–16)**
- [x] Repo created and structure set up
- [x] Codespace running
- [x] Cohort data loaded and validated
- [x] Historical retention calculation — working and validated against Sheet
- [x] Prediction factors — working and validated
- [x] Seasonality factors loaded (inputs/seasonality_factors.xlsx)
- [x] Future retention predictions — working end to end
- [x] New cohort projection (new users via seasonality) — working
- [x] Monthly totals output (active users by country x month) — working
- [ ] AOV and Frequency modules (next pieces of baseline)
- [ ] Validation/guardrails module (see "Open items" below)
- [ ] Move retention logic from exploration/ to models/baseline/ (production code)

**Working script:** exploration/retention_exploration.py — fully functional
end-to-end pipeline for User Retention: historical retention → prediction
factors → seasonality → future predictions → new cohorts → monthly totals.

**Validated against Sheet:** historical retention for CO Aug 2015 and Nov 2025
cohorts confirmed correct.

---

## Data

**Source (current):** Excel file `data/cohorts.xlsx` (NOT committed to git — in .gitignore)
**Source (future):** Snowflake — to be connected when enterprise account is set up
**Grain:** One row per COUNTRY x COHORT_MONTH x LT_MONTH x LT (after aggregating PRIME/NO PRIME)
**Countries:** AR, BR, CL, CO, CR, EC, MX, PE, UY
**Date range:** 2015-08-01 to present
**Original columns:** COHORT_MONTH, COUNTRY, PRIME_SUBSCRIPTION, LT_MONTH, LT, USERS, ORDERS, GMV

**Seasonality input:** `inputs/seasonality_factors.xlsx` (committed to git — stable input)
- One row per country (9 countries), one column per month
- Coverage: Feb 2021 to Dec 2030
- Same factor used for: (a) retention prediction, (b) new user/cohort projection

**Config:** `inputs/config.yml` — stable settings (prediction_end: 2030-12-01)

---

## Key business rules (full detail in docs/DECISIONS.md)

- PRIME_SUBSCRIPTION is aggregated away for the retention model (Decision 001)
- LATEST_CLOSED_MONTH = max COHORT_MONTH where LT=0 in the data — data-driven,
  not system clock (Decision 007)
- M0 retention = 100% by definition (LT=0 row)
- Retention formula: USERS(LT_x) / USERS(LT_0)
- Prediction formula: Future Retention(Mx) = Latest retention x Prediction factor x Seasonality
- Prediction factor = weighted avg (by M0_USERS) of retention(LT+1)/retention(LT)
  across cohorts that passed through that LT transition in last 12 months
- New cohorts: new_users(next month) = new_users(current month) x seasonality(next month),
  then projected forward like any other cohort starting at LT=0, retention=1.0
- Cohort activity gaps are normal for small countries, not excluded (Decision 008)
- Retention capped at 1.0 (can't exceed M0)

---

## Open items for Phase 1

### Validation / guardrails module
The model owner currently applies expert judgment to catch "weird" outputs
(growth far from trend, etc.). This needs to become an explicit module:

- `models/baseline/validation.py` — runs after every projection
- Categories: trend deviation checks (e.g. MoM growth vs 12-month trend),
  sanity bounds (e.g. retention <= 1.0, already implemented), structural/
  completeness checks (every country/month present)
- Produces a non-blocking "validation report" of flags for review
- Specific thresholds to be defined collaboratively once real predicted vs.
  historical comparisons are available
- Each agreed threshold gets logged in DECISIONS.md as an explicit business rule

### Remaining baseline components
- AOV (Average Order Value) projection logic — not yet discussed
- Frequency projection logic — not yet discussed
- Combine Retention x AOV x Frequency -> GMV, Orders, Users (final baseline output)

### Known data quality notes
- EC cohort 2019-07: only LT=0 exists, no further history (pre-dates
  seasonality table coverage — handled gracefully, produces 0 predicted rows)
- 17 cohorts (mostly CR, 11 of 17) show activity gaps before LATEST_CLOSED_MONTH —
  confirmed normal for small countries, not excluded from projection (Decision 008)

### Refactor to production code
- exploration/retention_exploration.py logic needs to move to models/baseline/
  with proper structure (base class pattern from architecture brief), plus tests

---

## Repo structure (current)