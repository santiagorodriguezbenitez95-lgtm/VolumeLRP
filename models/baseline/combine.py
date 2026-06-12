"""
models/baseline/combine.py

Combines the outputs of retention.py and frequency.py to produce:
  - cohort-level USERS and ORDERS predictions
  - monthly totals: TOTAL_USERS, TOTAL_ORDERS, and an aggregate FREQUENCY
    (TOTAL_ORDERS / TOTAL_USERS — NOT an average of cohort-level frequencies,
    see note below)

Requires retention.py and frequency.py to have been run first
(data/retention_full.csv and data/frequency_full.csv must exist).
"""

import pandas as pd

# ── 1. LOAD BOTH MODULES' OUTPUTS ────────────────────────────────
retention_df = pd.read_csv("data/retention_full.csv", parse_dates=["COHORT_MONTH", "LT_MONTH"])
frequency_df = pd.read_csv("data/frequency_full.csv", parse_dates=["COHORT_MONTH", "LT_MONTH"])

print(f"Retention rows: {len(retention_df):,}")
print(f"Frequency rows: {len(frequency_df):,}")

# ── 2. MERGE ON THE SHARED COHORT KEYS ───────────────────────────
# Both files have one row per (COUNTRY, COHORT_MONTH, LT, LT_MONTH).
# We only need RETENTION + M0_USERS from retention, and FREQUENCY from
# frequency — IS_PREDICTED is dropped from one side to avoid a duplicate
# column (suffixed _x/_y) since both files have it.
combined = retention_df[["COUNTRY", "COHORT_MONTH", "LT", "LT_MONTH", "RETENTION", "M0_USERS", "IS_PREDICTED"]].merge(
    frequency_df[["COUNTRY", "COHORT_MONTH", "LT", "LT_MONTH", "FREQUENCY"]],
    on=["COUNTRY", "COHORT_MONTH", "LT", "LT_MONTH"],
    how="inner"
)

print(f"Combined rows (inner join): {len(combined):,}")

# ── 3. COHORT-LEVEL USERS AND ORDERS ─────────────────────────────
combined["USERS"] = combined["RETENTION"] * combined["M0_USERS"]
combined["ORDERS"] = combined["FREQUENCY"] * combined["USERS"]

combined.to_csv("data/combined_full.csv", index=False)
print("\nSaved to data/combined_full.csv")

# ── 4. MONTHLY TOTALS ─────────────────────────────────────────────
# Aggregate FREQUENCY = TOTAL_ORDERS / TOTAL_USERS (user-weighted),
# NOT an average of cohort-level FREQUENCY values — a simple average
# would treat every cohort equally regardless of size.
monthly_totals = (
    combined
    .groupby(["COUNTRY", "LT_MONTH"])[["USERS", "ORDERS"]]
    .sum()
    .reset_index()
    .rename(columns={"USERS": "TOTAL_USERS", "ORDERS": "TOTAL_ORDERS"})
)
monthly_totals["FREQUENCY"] = monthly_totals["TOTAL_ORDERS"] / monthly_totals["TOTAL_USERS"]

monthly_totals.to_csv("data/monthly_totals_combined.csv", index=False)
print("Saved to data/monthly_totals_combined.csv")

# ── 5. SANITY CHECK ────────────────────────────────────────────────
print("\nCO - Monthly totals (2026):")
co_monthly = monthly_totals[monthly_totals["COUNTRY"] == "CO"].sort_values("LT_MONTH")
print(co_monthly[
    (co_monthly["LT_MONTH"] >= "2026-01-01") &
    (co_monthly["LT_MONTH"] <= "2026-12-01")
].to_string(index=False))