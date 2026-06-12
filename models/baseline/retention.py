"""
models/baseline/retention.py

User Retention module — historical retention -> prediction factors ->
seasonality -> future predictions (existing + new cohorts) -> monthly totals.

This is the same logic as exploration/retention_exploration.py, rewritten
to use the generic functions in pipeline/shared.py. Output should be
identical to the original script.
"""

import pandas as pd

from pipeline.shared import (
    load_and_aggregate_cohorts,
    load_and_validate_seasonality,
    calculate_prediction_factors,
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

# ── 3. HISTORICAL RETENTION ──────────────────────────────────────
m0 = (
    df[df["LT"] == 0][["COHORT_MONTH", "COUNTRY", "USERS"]]
    .rename(columns={"USERS": "M0_USERS"})
)

df = df.merge(m0, on=["COHORT_MONTH", "COUNTRY"], how="left")
df["RETENTION"] = df["USERS"] / df["M0_USERS"]

# ── 4. PREDICTION FACTORS ────────────────────────────────────────
prediction_factors = calculate_prediction_factors(
    df,
    metric_col="RETENTION",
    weight_col="M0_USERS",
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
print(f"Seasonality coverage: {seas['LT_MONTH'].min()} to {seas['LT_MONTH'].max()}")
print(f"Seasonality coverage validated - all months through {PREDICTION_END} covered")

# ── 7A. PROJECT EXISTING COHORTS FORWARD ────────────────────────
latest_known = (
    df.sort_values("LT")
    .groupby(["COUNTRY", "COHORT_MONTH"])
    .tail(1)
)[["COUNTRY", "COHORT_MONTH", "LT", "LT_MONTH", "RETENTION", "M0_USERS"]]

print(f"\nExisting cohorts to project: {len(latest_known):,}")

# Informational only (Decision 008): some cohorts (mostly small countries)
# show last activity before LATEST_CLOSED_MONTH. Normal — projected from
# last known point regardless.
gaps = latest_known[latest_known["LT_MONTH"] != LATEST_CLOSED_MONTH]
if not gaps.empty:
    print(f"\nINFO: {len(gaps)} cohorts last observed before {LATEST_CLOSED_MONTH} "
          f"(normal for small countries — projecting from last known point):")
    print(gaps[["COUNTRY", "COHORT_MONTH", "LT", "LT_MONTH"]].to_string(index=False))

predictions = []
for _, row in latest_known.iterrows():
    predictions.extend(project_metric(
        country=row["COUNTRY"],
        cohort_month=row["COHORT_MONTH"],
        start_lt=row["LT"],
        start_lt_month=row["LT_MONTH"],
        start_value=row["RETENTION"],
        prediction_factors=prediction_factors,
        seasonality=seas,
        prediction_end=PREDICTION_END,
        metric_col="RETENTION",
        cap_at_one=True,
        extra_cols={"M0_USERS": row["M0_USERS"]},
    ))

predictions_df = pd.DataFrame(predictions)
print(f"\nPredicted rows for existing cohorts: {len(predictions_df):,}")

# ── 7B. PROJECT NEW COHORTS (FUTURE NEW USERS) ──────────────────
# Baseline assumption: new users evolve via seasonality only.
#   new_users(next month) = new_users(current month) x seasonality(next month)
# Each new cohort then gets its own retention curve via project_metric,
# starting from LT=0, RETENTION=1.0 (by definition for retention).

new_cohort_rows = []

for country in df["COUNTRY"].unique():
    latest_cohort = df[
        (df["COUNTRY"] == country) &
        (df["COHORT_MONTH"] == LATEST_CLOSED_MONTH) &
        (df["LT"] == 0)
    ]

    if latest_cohort.empty:
        print(f"WARNING: No M0 data for {country} at {LATEST_CLOSED_MONTH} - skipping new cohorts")
        continue

    current_new_users = latest_cohort["M0_USERS"].values[0]
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
        next_new_users = current_new_users * seasonality

        # New cohort's M0 row
        new_cohort_rows.append({
            "COUNTRY": country,
            "COHORT_MONTH": next_month,
            "LT": 0,
            "LT_MONTH": next_month,
            "RETENTION": 1.0,
            "M0_USERS": next_new_users,
            "IS_PREDICTED": True
        })

        # Project this new cohort's retention forward too
        new_cohort_rows.extend(project_metric(
            country=country,
            cohort_month=next_month,
            start_lt=0,
            start_lt_month=next_month,
            start_value=1.0,
            prediction_factors=prediction_factors,
            seasonality=seas,
            prediction_end=PREDICTION_END,
            metric_col="RETENTION",
            cap_at_one=True,
            extra_cols={"M0_USERS": next_new_users},
        ))

        current_new_users = next_new_users
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
][["LT", "LT_MONTH", "RETENTION", "IS_PREDICTED"]]

print("\nCO - November 2025 cohort (historical + predicted):")
print(co_nov_2025.to_string(index=False))

first_new_cohort_month = LATEST_CLOSED_MONTH + pd.DateOffset(months=1)
co_first_new = full_df[
    (full_df["COUNTRY"] == "CO") &
    (full_df["COHORT_MONTH"] == first_new_cohort_month)
][["LT", "LT_MONTH", "RETENTION", "M0_USERS", "IS_PREDICTED"]]

print(f"\nCO - first new cohort ({first_new_cohort_month.date()}, fully predicted):")
print(co_first_new.head(10).to_string(index=False))

# ── 10. SAVE OUTPUT ──────────────────────────────────────────────
full_df.to_csv("data/retention_full.csv", index=False)
print("\nSaved to data/retention_full.csv")