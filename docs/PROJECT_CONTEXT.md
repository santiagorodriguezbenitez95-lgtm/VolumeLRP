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
   via AOV x frequency x retention logic.
   - User Retention: DONE (see below)
   - Frequency: DONE (see below)
   - Users + Orders combination: DONE (see below)
   - AOV: not started — BLOCKED on FX/local-currency decision (see "Open items")
   - GMV (= Orders x AOV): not started, depends on AOV
2. **Micro-models (~30)** — each estimates volume and cost impact of improving
   a specific growth lever. Mix of structurally similar and heterogeneous models.
   Not started.
3. **Cannibalization** — estimates overlap between levers. Not started.
4. **Zone-level Appendix** — translates country-level outputs to zone-level targets.
   Not started.

---

## Current status

**Phase 0 — Foundations (Jun 11–16): COMPLETE**
- [x] Repo created and structure set up
- [x] Codespace running
- [x] Cohort data loaded and validated
- [x] Historical retention calculation — working and validated against Sheet
- [x] Prediction factors — working and validated
- [x] Seasonality factors loaded (inputs/seasonality_factors.xlsx)
- [x] Future retention predictions — working end to end
- [x] New cohort projection (new users via seasonality) — working
- [x] Retention logic refactored into production structure (pipeline/ + models/baseline/)

**Phase 1 — Frequency & Combine: IN PROGRESS**
- [x] pipeline/shared.py created — generalized functions extracted from
      retention logic (load/aggregate, seasonality, weighted_average,
      prediction factors, project_metric)
- [x] models/baseline/retention.py — refactored to use shared.py (behavior
      should be unchanged from exploration/retention_exploration.py; monthly
      totals section removed, now handled by combine.py)
- [x] models/baseline/frequency.py — historical frequency, prediction factors,
      seasonality projection, new cohort seeding (built, NOT YET VALIDATED
      against Sheet)
- [x] models/baseline/combine.py — merges retention + frequency outputs into
      cohort-level USERS/ORDERS and monthly totals (TOTAL_USERS, TOTAL_ORDERS,
      aggregate FREQUENCY) (built, NOT YET VALIDATED against Sheet)
- [ ] Run retention -> frequency -> combine end to end in Codespace and
      validate monthly totals against Sheet (CO 2026 as reference)
- [ ] Commit and push Phase 1 code to GitHub (first git workflow walkthrough
      pending)
- [ ] Validation/guardrails module (see "Open items" below)

**Not yet started:**
- [ ] FX / local currency conversion (see "Open items" — blocking AOV)
- [ ] AOV and GMV modules

---

## Data

**Source (current):** Excel file `data/cohorts.xlsx` (NOT committed to git — in .gitignore)
**Source (future):** Snowflake — to be connected when enterprise account is set up
**Grain:** One row per COUNTRY x COHORT_MONTH x LT_MONTH x LT (after aggregating PRIME/NO PRIME)
**Countries:** AR, BR, CL, CO, CR, EC, MX, PE, UY
**Date range:** 2015-08-01 to present
**Original columns:** COHORT_MONTH, COUNTRY, PRIME_SUBSCRIPTION, LT_MONTH, LT, USERS, ORDERS, GMV
**Currency:** GMV (and therefore AOV) is currently in USD — known issue,
see "Open items"

**Seasonality input:** `inputs/seasonality_factors.xlsx` (committed to git — stable input)
- One row per country (9 countries), one column per month
- Coverage: Feb 2021 to Dec 2030
- Same factor used for: (a) retention prediction, (b) frequency prediction,
  (c) new user/cohort projection, (d) new cohort frequency seeding

**FX input:** historical FX rates available (held by model owner), not yet
loaded into the repo — needed for local-currency conversion (see "Open items")

**Config:** `inputs/config.yml` — stable settings (prediction_end: 2030-12-01)

---

## Key business rules (full detail in docs/DECISIONS.md)

- PRIME_SUBSCRIPTION is aggregated away for retention and frequency models (Decision 001)
- LATEST_CLOSED_MONTH = max COHORT_MONTH where LT=0 in the data — data-driven,
  not system clock (Decision 007)
- M0 retention = 100% by definition (LT=0 row)
- Retention formula: USERS(LT_x) / USERS(LT_0), capped at 1.0
- Retention prediction factor: weighted avg (by M0_USERS) of retention(LT+1)/retention(LT)
  across cohorts that passed through that LT transition in last 12 months
- Frequency formula: ORDERS(LT) / USERS(LT) — row-wise, no cohort reference needed
- Frequency prediction factor: weighted avg (by USERS(LT), i.e. actual active
  users at that transition, NOT M0_USERS) of frequency(LT+1)/frequency(LT),
  last 12 months — no cap (Decision 010)
- New cohorts: new_users(next month) = new_users(current month) x seasonality(next month),
  then projected forward like any other cohort starting at LT=0, retention=1.0
- New cohort frequency seeding: LT=0 frequency for the FIRST predicted month
  = weighted avg (by USERS) of LT=0 frequency over last 12 months; every
  subsequent new cohort's LT=0 frequency = previous cohort's LT=0 frequency
  x seasonality(this month) (Decision 011)
- Combine step: USERS = RETENTION x M0_USERS; ORDERS = FREQUENCY x USERS;
  aggregate monthly FREQUENCY = TOTAL_ORDERS / TOTAL_USERS, NOT an average
  of cohort-level frequencies (Decision 012)
- Cohort activity gaps are normal for small countries, not excluded (Decision 008)

---

## Open items for Phase 1

### FX / local currency conversion (BLOCKING AOV) — Decision pending formalization
GMV (and AOV = GMV/ORDERS) is currently in USD, which mixes two signals:
real local pricing/behavior trends AND FX rate changes. Before building AOV,
cohort GMV needs to be converted to local currency using historical FX rates
(model owner has these). This is a TEMPORARY fix — the long-term correct
solution is for BI to provide GMV in local currency at the source, but that
has an unknown timeline and would block AOV work for too long.
**Plan:** build an isolated `pipeline/fx_conversion.py` module that converts
GMV to local currency before AOV calculations, applied only where currency
matters (AOV/GMV — NOT retention or frequency, which are USERS/ORDERS based
and currency-independent). To be replaced/removed once BI fixes the raw
data source. Log as Decision 013 once implemented.

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
- FX/local currency conversion (see above) — next immediate step
- AOV (Average Order Value) projection logic — not yet discussed, blocked on FX
- GMV = Orders x AOV — depends on AOV

### Known data quality notes
- EC cohort 2019-07: only LT=0 exists, no further history (pre-dates
  seasonality table coverage — handled gracefully, produces 0 predicted rows)
- 17 cohorts (mostly CR, 11 of 17) show activity gaps before LATEST_CLOSED_MONTH —
  confirmed normal for small countries, not excluded from projection (Decision 008)

---

## Repo structure (current)

```
VolumeLRP/
  ├── pipeline/
  │   ├── __init__.py
  │   └── shared.py            # generic functions: load/aggregate, seasonality,
  │                             # weighted_average, prediction factors, project_metric
  ├── models/
  │   ├── __init__.py
  │   └── baseline/
  │       ├── __init__.py
  │       ├── retention.py     # User Retention module (uses pipeline/shared.py)
  │       ├── frequency.py      # Frequency module (uses pipeline/shared.py)
  │       └── combine.py        # Merges retention + frequency -> USERS/ORDERS,
  │                              # monthly totals
  ├── app/                      # empty so far (Phase 5)
  ├── exploration/
  │   └── retention_exploration.py   # original working script — superseded by
  │                                    # models/baseline/retention.py, kept for reference
  ├── data/                     # NOT in git — cohorts.xlsx, output CSVs
  ├── inputs/                   # committed — stable model inputs
  │   ├── seasonality_factors.xlsx
  │   └── config.yml
  ├── tests/                    # empty so far
  ├── docs/
  │   ├── PROJECT_CONTEXT.md    # <- you are here
  │   └── DECISIONS.md
  └── Makefile
```

---

## Claude tool recommendations by phase

| Phase | Tool | Model |
|---|---|---|
| 0–2 planning | Claude.ai chat | Sonnet |
| 1–2 formula translation | Claude.ai Projects | Opus |
| 1–4 coding | Claude Code | Sonnet |
| 3 cannibalization | Enterprise Projects | Opus |
| 5 UI build | Claude Code | Sonnet |

---

## Working style notes

- Model owner is learning AI-assisted development from scratch (second major AI project)
- Also new to git/GitHub — first real project using them; explain git/terminal
  concepts as they come up (commits, push, branches, etc.)
- Teaching approach: explain concepts as we go (git, pandas, terminal, AI
  workflow decisions, etc.) — this applies throughout, not just Phase 0
- Correct English as we work (non-native speaker) — ongoing throughout
- Always profile real data before writing code
- Validate each layer before building the next, rather than building
  everything then debugging
- For shared logic across modules (e.g. retention + frequency both needing
  prediction factors and projection), generalize into pipeline/shared.py
  rather than duplicating — refactor, then re-validate against Sheet to
  confirm behavior unchanged
- exploration/ = throwaway/messy scripts; models/ = production code