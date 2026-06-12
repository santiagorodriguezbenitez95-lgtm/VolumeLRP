"""
models/baseline/frequency.py

Frequency module — historical frequency -> prediction factors -> seasonality
-> future predictions (existing + new cohorts).

Mirrors retention.py's structure, reusing the same pipeline/shared.py
functions, but with frequency-specific logic:
  - FREQUENCY = ORDERS / USERS (no M0 merge needed)
  - prediction factors weighted by USERS(LT), not M0_USERS
  - no cap at 1.0 (frequency can exceed 1)
  - new cohorts' LT=0 frequency is seeded from a weighted average of the
    last 12 months' LT=0 frequency, then chained forward via seasonality
    (same chaining pattern as new users in retention.py)
"""

import pandas as pd

from pipeline.shared import (
    load_and_aggregate_cohorts,
    load_and_validate_seasonality,
    calculate_prediction_factors,
    weighted_average,
    project_metric,
    load_config,
)

# ── PARAMETERS ───────────────────────────────────────────────────
config = load_config("inputs/config.yml")
PREDICTION_END = pd.Timestamp(config["prediction_end"])
print(f"Prediction horizon: {PREDICTION_END}")

# ── 1-2. LOAD, AGGREGATE, APPLY CUTOFF ───────────────────────────
df, LATEST_CLOSED_MONTH = load_and_aggregate_cohorts("data/cohorts.xlsx")
print(f"Latest closed month: {LATEST_CLOSED_MONTH}")
print(f"Total rows after cutoff: {len(df):,}")

# ── 3. HISTORICAL FREQUENCY ───────────────────────────────────────
# Unlike retention (which divides by M0_USERS from a separate row),
# frequency is a simple row-wise division — no merge needed.
df["FREQUENCY"] = df["ORDERS"] / df["USERS"]

# ── 4. PREDICTION FACTORS ────────────────────────────────────────
# Weighted by USERS(LT) — the actual active users at each transition —
# not M0_USERS (Decision: frequency prediction factor weighting).
prediction_factors = calculate_prediction_factors(
    df,
    metric_col="FREQUENCY",
    weight_col="USERS",
    latest_closed_month=LATEST_CLOSED_MONTH,
)
print(f"Prediction factors calculated: {len(prediction_factors):,}")

# ── 5-6. LOAD & VALIDATE SEASONALITY ─────────────────────────────
seas = load_and_validate_seasonality(
    "inputs/seasonality_factors.xlsx",
    latest_closed_month=LATEST_CLOSED_MONTH,
    prediction_end=PREDICTION_END,
)
print(f"Seasonality factors loaded: {len(seas):,} rows")

# ── 7A. PROJECT EXISTING COHORTS FORWARD ────────────────────────
latest_known = (
    df.sort_values("LT")
    .groupby(["COUNTRY", "COHORT_MONTH"])
    .tail(1)
)[["COUNTRY", "COHORT_MONTH", "LT", "LT_MONTH", "FREQUENCY"]]

print(f"\nExisting cohorts to project: {len(latest_known):,}")

predictions = []
for _, row in latest_known.iterrows():
    predictions.extend(project_metric(
        country=row["COUNTRY"],
        cohort_month=row["COHORT_MONTH"],
        start_lt=row["LT"],
        start_lt_month=row["LT_MONTH"],
        start_value=row["FREQUENCY"],
        prediction_factors=prediction_factors,
        seasonality=seas,
        prediction_end=PREDICTION_END,
        metric_col="FREQUENCY",
        cap_at_one=False,
    ))

predictions_df = pd.DataFrame(predictions)
print(f"\nPredicted rows for existing cohorts: {len(predictions_df):,}")

# ── 7B. PROJECT NEW COHORTS ───────────────────────────────────────
# New cohorts' LT=0 FREQUENCY:
#   - First predicted month: weighted average (by USERS) of LT=0 FREQUENCY
#     over the last 12 months — a smoothed starting point.
#   - Every month after that: previous month's LT=0 FREQUENCY x
#     seasonality(this month) — same chaining pattern as new users.
# From LT=0, each new cohort is projected forward like any other cohort.

lookback_start = pd.Timestamp(LATEST_CLOSED_MONTH) - pd.DateOffset(months=12)
m0_recent = df[(df["LT"] == 0) & (df["LT_MONTH"] > lookback_start)]

seed = weighted_average(m0_recent, value_col="FREQUENCY", weight_col="USERS", group_cols=["COUNTRY"])
seed = seed.set_index("COUNTRY")["FREQUENCY_AVG"]  # COUNTRY -> seed FREQUENCY

new_cohort_rows = []

for country in df["COUNTRY"].unique():
    if country not in seed.index:
        print(f"WARNING: No LT=0 history for {country} in last 12 months - skipping new cohorts")
        continue

    current_frequency = None  # set on first iteration
    current_month = LATEST_CLOSED_MONTH

    while True:
        next_month = current_month + pd.DateOffset(months=1)
        if next_month > PREDICTION_END:
            break

        sf_row = seas[
            (seas["COUNTRY"] == country) &
            (seas["LT_MONTH"] == next_month)
        ]
        seasonality = sf_row["SEASONALITY"].values[0]

        if current_frequency is None:
            # First predicted month: use the smoothed seed directly
            # (not multiplied by seasonality - the seed IS the starting point)
            next_frequency = seed[country]
        else:
            next_frequency = current_frequency * seasonality

        # New cohort's M0 row
        new_cohort_rows.append({
            "COUNTRY": country,
            "COHORT_MONTH": next_month,
            "LT": 0,
            "LT_MONTH": next_month,
            "FREQUENCY": next_frequency,
            "IS_PREDICTED": True
        })

        # Project this new cohort's frequency forward too
        new_cohort_rows.extend(project_metric(
            country=country,
            cohort_month=next_month,
            start_lt=0,
            start_lt_month=next_month,
            start_value=next_frequency,
            prediction_factors=prediction_factors,
            seasonality=seas,
            prediction_end=PREDICTION_END,
            metric_col="FREQUENCY",
            cap_at_one=False,
        ))

        current_frequency = next_frequency
        current_month = next_month

new_cohorts_df = pd.DataFrame(new_cohort_rows)
print(f"New cohort rows (M0 + projected): {len(new_cohorts_df):,}")

# ── 8. COMBINE HISTORICAL + PREDICTED ───────────────────────────
df["IS_PREDICTED"] = False
full_df = pd.concat([df, predictions_df, new_cohorts_df], ignore_index=True)
full_df = full_df.sort_values(["COUNTRY", "COHORT_MONTH", "LT"]).reset_index(drop=True)

print(f"\nTotal rows (historical + predicted): {len(full_df):,}")

# ── 9. SANITY CHECKS ─────────────────────────────────────────────
co_nov_2025 = full_df[
    (full_df["COUNTRY"] == "CO") &
    (full_df["COHORT_MONTH"] == "2025-11-01")
][["LT", "LT_MONTH", "FREQUENCY", "IS_PREDICTED"]]

print("\nCO - November 2025 cohort (historical + predicted):")
print(co_nov_2025.to_string(index=False))

first_new_cohort_month = LATEST_CLOSED_MONTH + pd.DateOffset(months=1)
co_first_new = full_df[
    (full_df["COUNTRY"] == "CO") &
    (full_df["COHORT_MONTH"] == first_new_cohort_month)
][["LT", "LT_MONTH", "FREQUENCY", "IS_PREDICTED"]]

print(f"\nCO - first new cohort ({first_new_cohort_month.date()}, fully predicted):")
print(co_first_new.head(10).to_string(index=False))

print(f"\nCO - seed FREQUENCY for first new cohort: {seed['CO']:.4f}")

# ── 10. SAVE OUTPUT ──────────────────────────────────────────────
full_df.to_csv("data/frequency_full.csv", index=False)
print("\nSaved to data/frequency_full.csv")