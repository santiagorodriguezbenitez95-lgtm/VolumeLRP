import pandas as pd
import yaml

# ── PARAMETERS ───────────────────────────────────────────────────
LATEST_CLOSED_MONTH = None  # Auto-detected below from data

with open("inputs/config.yml", "r") as f:
    config = yaml.safe_load(f)

PREDICTION_END = pd.Timestamp(config["prediction_end"])
print(f"Prediction horizon: {PREDICTION_END}")

# ── 1. LOAD & AGGREGATE ──────────────────────────────────────────
df = pd.read_excel("data/cohorts.xlsx")

df["LT_MONTH"] = pd.to_datetime(df["LT_MONTH"])
df["COHORT_MONTH"] = pd.to_datetime(df["COHORT_MONTH"])

# Aggregate PRIME + NO PRIME — not used in retention model
df = (
    df.groupby(["COHORT_MONTH", "COUNTRY", "LT_MONTH", "LT"])[["USERS", "ORDERS", "GMV"]]
    .sum()
    .reset_index()
)

# ── 2. APPLY CUTOFF ──────────────────────────────────────────────
# Latest closed month = most recent month for which a new cohort exists.
# Derived from data, not the system clock — robust and self-correcting.
if LATEST_CLOSED_MONTH is None:
    LATEST_CLOSED_MONTH = df[df["LT"] == 0]["COHORT_MONTH"].max()

df = df[df["LT_MONTH"] <= LATEST_CLOSED_MONTH]
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
# Weighted average (by M0_USERS) of retention(LT+1)/retention(LT)
# across all cohorts that passed through that transition in the
# last 12 months. One factor per COUNTRY x LT transition.
lookback_start = pd.Timestamp(LATEST_CLOSED_MONTH) - pd.DateOffset(months=12)
recent = df[df["LT_MONTH"] > lookback_start].copy()

current_lt_df = recent[["COUNTRY", "COHORT_MONTH", "LT", "RETENTION", "M0_USERS"]].copy()
next_lt_df = recent[["COUNTRY", "COHORT_MONTH", "LT", "RETENTION"]].copy()
next_lt_df = next_lt_df.rename(columns={"LT": "LT_NEXT", "RETENTION": "RETENTION_NEXT"})
next_lt_df["LT"] = next_lt_df["LT_NEXT"] - 1

transitions = current_lt_df.merge(next_lt_df, on=["COUNTRY", "COHORT_MONTH", "LT"], how="inner")
transitions = transitions[transitions["RETENTION"] > 0]
transitions["RATIO"] = transitions["RETENTION_NEXT"] / transitions["RETENTION"]
transitions["WEIGHTED_RATIO"] = transitions["RATIO"] * transitions["M0_USERS"]

prediction_factors = (
    transitions
    .groupby(["COUNTRY", "LT"])
    .agg(
        TOTAL_WEIGHTED=("WEIGHTED_RATIO", "sum"),
        TOTAL_WEIGHT=("M0_USERS", "sum")
    )
    .reset_index()
)
prediction_factors["PREDICTION_FACTOR"] = (
    prediction_factors["TOTAL_WEIGHTED"] / prediction_factors["TOTAL_WEIGHT"]
)
prediction_factors = prediction_factors[["COUNTRY", "LT", "PREDICTION_FACTOR"]]
print(f"Prediction factors calculated: {len(prediction_factors):,}")

# ── 5. LOAD SEASONALITY FACTORS ──────────────────────────────────
# Stable model input — never changes. Covers 2021-02 through 2030-12,
# all 9 countries. Same factor used for retention prediction AND
# new user / new cohort projection.
seas_raw = pd.read_excel("inputs/seasonality_factors.xlsx", index_col=0)

seas = seas_raw.reset_index().copy()
seas = seas.rename(columns={"Month Composition": "COUNTRY"})
seas = seas.melt(id_vars="COUNTRY", var_name="LT_MONTH", value_name="SEASONALITY")
seas["LT_MONTH"] = pd.to_datetime(seas["LT_MONTH"])

print(f"Seasonality factors loaded: {len(seas):,} rows")
print(f"Seasonality coverage: {seas['LT_MONTH'].min()} to {seas['LT_MONTH'].max()}")

# ── 6. VALIDATE SEASONALITY COVERAGE ────────────────────────────
needed_months = pd.date_range(
    start=LATEST_CLOSED_MONTH + pd.DateOffset(months=1),
    end=PREDICTION_END,
    freq="MS"
)
missing = [m for m in needed_months if m not in seas["LT_MONTH"].values]
if missing:
    raise ValueError(
        f"Seasonality factors missing for {len(missing)} months: "
        f"{missing[0]} to {missing[-1]}. "
        f"Please update inputs/seasonality_factors.xlsx before running."
    )
print(f"Seasonality coverage validated - all months through {PREDICTION_END} covered")

# ── 7. PROJECTION FUNCTION ───────────────────────────────────────
def project_cohort(country, cohort_month, start_lt, start_lt_month, start_retention, m0_users):
    """
    Projects a single cohort's retention forward from (start_lt, start_retention)
    until PREDICTION_END, using prediction factors and seasonality:

        Future Retention(Mx) = Latest retention x Prediction factor x Seasonality

    Stops early if no prediction factor or seasonality factor is available
    (e.g. dates before the seasonality table's coverage, or LT ages with
    no recent transition history). Returns a list of predicted rows.
    """
    rows = []
    current_lt = start_lt
    current_retention = start_retention
    current_lt_month = start_lt_month

    while True:
        next_lt = current_lt + 1
        next_lt_month = current_lt_month + pd.DateOffset(months=1)

        if next_lt_month > PREDICTION_END:
            break

        pf_row = prediction_factors[
            (prediction_factors["COUNTRY"] == country) &
            (prediction_factors["LT"] == current_lt)
        ]
        if pf_row.empty:
            break

        sf_row = seas[
            (seas["COUNTRY"] == country) &
            (seas["LT_MONTH"] == next_lt_month)
        ]
        if sf_row.empty:
            break

        prediction_factor = pf_row["PREDICTION_FACTOR"].values[0]
        seasonality = sf_row["SEASONALITY"].values[0]

        next_retention = current_retention * prediction_factor * seasonality
        next_retention = min(next_retention, 1.0)  # can't exceed M0

        rows.append({
            "COUNTRY": country,
            "COHORT_MONTH": cohort_month,
            "LT": next_lt,
            "LT_MONTH": next_lt_month,
            "RETENTION": next_retention,
            "M0_USERS": m0_users,
            "IS_PREDICTED": True
        })

        current_lt = next_lt
        current_retention = next_retention
        current_lt_month = next_lt_month

    return rows


# ── 7A. PROJECT EXISTING COHORTS FORWARD ────────────────────────
latest_known = (
    df.sort_values("LT")
    .groupby(["COUNTRY", "COHORT_MONTH"])
    .tail(1)
)[["COUNTRY", "COHORT_MONTH", "LT", "LT_MONTH", "RETENTION", "M0_USERS"]]

print(f"\nExisting cohorts to project: {len(latest_known):,}")

# Informational only (Decision 008): some cohorts (mostly small countries)
# show last activity before LATEST_CLOSED_MONTH. Normal — a cohort can go
# quiet and resume. Projected from last known point regardless.
gaps = latest_known[latest_known["LT_MONTH"] != LATEST_CLOSED_MONTH]
if not gaps.empty:
    print(f"\nINFO: {len(gaps)} cohorts last observed before {LATEST_CLOSED_MONTH} "
          f"(normal for small countries — projecting from last known point):")
    print(gaps[["COUNTRY", "COHORT_MONTH", "LT", "LT_MONTH"]].to_string(index=False))

predictions = []
for _, row in latest_known.iterrows():
    predictions.extend(project_cohort(
        country=row["COUNTRY"],
        cohort_month=row["COHORT_MONTH"],
        start_lt=row["LT"],
        start_lt_month=row["LT_MONTH"],
        start_retention=row["RETENTION"],
        m0_users=row["M0_USERS"]
    ))

predictions_df = pd.DataFrame(predictions)
print(f"\nPredicted rows for existing cohorts: {len(predictions_df):,}")


# ── 7B. PROJECT NEW COHORTS (FUTURE NEW USERS) ──────────────────
# Baseline assumption: new users evolve via seasonality only.
#   new_users(next month) = new_users(current month) x seasonality(next month)
# Each new cohort then gets its own retention curve via project_cohort,
# starting from LT=0, RETENTION=1.0.

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
        new_cohort_rows.extend(project_cohort(
            country=country,
            cohort_month=next_month,
            start_lt=0,
            start_lt_month=next_month,
            start_retention=1.0,
            m0_users=next_new_users
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

# ── 11. MONTHLY TOTALS ───────────────────────────────────────────
# Collapses the cohort dimension: total active users per country per
# calendar month, summed across ALL cohorts active that month.
# This is the bridge from cohort-level engine to business-facing output.
full_df["ACTIVE_USERS"] = full_df["RETENTION"] * full_df["M0_USERS"]

monthly_totals = (
    full_df
    .groupby(["COUNTRY", "LT_MONTH"])["ACTIVE_USERS"]
    .sum()
    .reset_index()
    .rename(columns={"ACTIVE_USERS": "TOTAL_USERS"})
)

print("\nCO - Monthly total users (2026):")
co_monthly = monthly_totals[monthly_totals["COUNTRY"] == "CO"].sort_values("LT_MONTH")
print(co_monthly[
    (co_monthly["LT_MONTH"] >= "2026-01-01") &
    (co_monthly["LT_MONTH"] <= "2026-12-01")
].to_string(index=False))

monthly_totals.to_csv("data/monthly_totals.csv", index=False)
print("\nSaved to data/monthly_totals.csv")