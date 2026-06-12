"""
pipeline/shared.py

Shared building blocks used by both the Retention and Frequency modules
(and by future modules that follow the same "historical -> prediction
factors -> seasonality -> projection" pattern).

Nothing in this file is specific to retention OR frequency — it only
knows about generic concepts: cohorts, metrics, weights, and seasonality.
"""

import pandas as pd
import yaml


# ── 1. LOAD & AGGREGATE COHORT DATA ──────────────────────────────
def load_and_aggregate_cohorts(path="data/cohorts.xlsx", latest_closed_month=None):
    """
    Loads the raw cohorts file, aggregates away PRIME_SUBSCRIPTION
    (Decision 001), and applies the LATEST_CLOSED_MONTH cutoff
    (Decision 007).

    If latest_closed_month is None, it is auto-detected from the data
    (max COHORT_MONTH where LT=0).

    Returns (df, latest_closed_month).
    """
    df = pd.read_excel(path)

    df["LT_MONTH"] = pd.to_datetime(df["LT_MONTH"])
    df["COHORT_MONTH"] = pd.to_datetime(df["COHORT_MONTH"])

    df = (
        df.groupby(["COHORT_MONTH", "COUNTRY", "LT_MONTH", "LT"])[["USERS", "ORDERS", "GMV"]]
        .sum()
        .reset_index()
    )

    if latest_closed_month is None:
        latest_closed_month = df[df["LT"] == 0]["COHORT_MONTH"].max()

    df = df[df["LT_MONTH"] <= latest_closed_month]

    return df, latest_closed_month


# ── 2. LOAD & VALIDATE SEASONALITY ───────────────────────────────
def load_and_validate_seasonality(path, latest_closed_month, prediction_end):
    """
    Loads inputs/seasonality_factors.xlsx and reshapes it from
    "one column per month" to "one row per (COUNTRY, LT_MONTH)".

    Validates that every month from latest_closed_month+1 through
    prediction_end is covered (Decision 005) — raises ValueError if not.

    Returns the seasonality dataframe with columns:
    COUNTRY, LT_MONTH, SEASONALITY
    """
    seas_raw = pd.read_excel(path, index_col=0)

    seas = seas_raw.reset_index().copy()
    seas = seas.rename(columns={"Month Composition": "COUNTRY"})
    seas = seas.melt(id_vars="COUNTRY", var_name="LT_MONTH", value_name="SEASONALITY")
    seas["LT_MONTH"] = pd.to_datetime(seas["LT_MONTH"])

    needed_months = pd.date_range(
        start=latest_closed_month + pd.DateOffset(months=1),
        end=prediction_end,
        freq="MS"
    )
    missing = [m for m in needed_months if m not in seas["LT_MONTH"].values]
    if missing:
        raise ValueError(
            f"Seasonality factors missing for {len(missing)} months: "
            f"{missing[0]} to {missing[-1]}. "
            f"Please update {path} before running."
        )

    return seas


# ── 3. WEIGHTED AVERAGE ──────────────────────────────────────────
def weighted_average(df, value_col, weight_col, group_cols):
    """
    Weighted average of value_col, weighted by weight_col, grouped by
    group_cols.

    Example: weighted_average(transitions, value_col="RATIO",
                               weight_col="M0_USERS", group_cols=["COUNTRY", "LT"])
    returns one row per (COUNTRY, LT) with a column "RATIO_AVG".

    Used by calculate_prediction_factors (averaging a ratio) and by
    the new-cohort seed calculation (averaging a raw metric value) —
    same mechanism, different inputs.
    """
    work = df.copy()
    work["_weighted"] = work[value_col] * work[weight_col]

    result = (
        work.groupby(group_cols)
        .agg(_sum_weighted=("_weighted", "sum"), _sum_weight=(weight_col, "sum"))
        .reset_index()
    )
    result[f"{value_col}_AVG"] = result["_sum_weighted"] / result["_sum_weight"]

    return result[group_cols + [f"{value_col}_AVG"]]


# ── 4. PREDICTION FACTORS ────────────────────────────────────────
def calculate_prediction_factors(df, metric_col, weight_col, latest_closed_month, lookback_months=12):
    """
    Weighted average (by weight_col) of metric(LT+1)/metric(LT), across
    cohorts that passed through that (COUNTRY, LT) transition in the
    last `lookback_months` months. One factor per (COUNTRY, LT).

    For retention: metric_col="RETENTION", weight_col="M0_USERS"
    For frequency: metric_col="FREQUENCY", weight_col="USERS"

    Returns a dataframe with columns: COUNTRY, LT, PREDICTION_FACTOR
    """
    lookback_start = pd.Timestamp(latest_closed_month) - pd.DateOffset(months=lookback_months)
    recent = df[df["LT_MONTH"] > lookback_start].copy()

    current_lt_df = recent[["COUNTRY", "COHORT_MONTH", "LT", metric_col, weight_col]].copy()
    next_lt_df = recent[["COUNTRY", "COHORT_MONTH", "LT", metric_col]].copy()
    next_lt_df = next_lt_df.rename(columns={"LT": "LT_NEXT", metric_col: f"{metric_col}_NEXT"})
    next_lt_df["LT"] = next_lt_df["LT_NEXT"] - 1

    transitions = current_lt_df.merge(next_lt_df, on=["COUNTRY", "COHORT_MONTH", "LT"], how="inner")
    transitions = transitions[transitions[metric_col] > 0]
    transitions["RATIO"] = transitions[f"{metric_col}_NEXT"] / transitions[metric_col]

    averaged = weighted_average(transitions, value_col="RATIO", weight_col=weight_col, group_cols=["COUNTRY", "LT"])
    averaged = averaged.rename(columns={"RATIO_AVG": "PREDICTION_FACTOR"})

    return averaged


# ── 5. PROJECT A SINGLE COHORT'S METRIC FORWARD ──────────────────
def project_metric(country, cohort_month, start_lt, start_lt_month, start_value,
                    prediction_factors, seasonality, prediction_end,
                    metric_col, cap_at_one=False, extra_cols=None):
    """
    Projects a single cohort's metric forward from (start_lt, start_value)
    until prediction_end, using prediction factors and seasonality:

        Future metric(Mx) = Latest metric x Prediction factor x Seasonality

    Stops early if no prediction factor or seasonality factor is available.

    - metric_col: name to use for the projected value in the output rows
      (e.g. "RETENTION" or "FREQUENCY")
    - cap_at_one: if True, projected values are capped at 1.0
      (retention only — Decision: retention can't exceed M0)
    - extra_cols: dict of additional static values to attach to every
      output row (e.g. {"M0_USERS": m0_users} for retention)

    Returns a list of predicted row dicts.
    """
    extra_cols = extra_cols or {}
    rows = []

    current_lt = start_lt
    current_value = start_value
    current_lt_month = start_lt_month

    while True:
        next_lt = current_lt + 1
        next_lt_month = current_lt_month + pd.DateOffset(months=1)

        if next_lt_month > prediction_end:
            break

        pf_row = prediction_factors[
            (prediction_factors["COUNTRY"] == country) &
            (prediction_factors["LT"] == current_lt)
        ]
        if pf_row.empty:
            break

        sf_row = seasonality[
            (seasonality["COUNTRY"] == country) &
            (seasonality["LT_MONTH"] == next_lt_month)
        ]
        if sf_row.empty:
            break

        prediction_factor = pf_row["PREDICTION_FACTOR"].values[0]
        seasonality_factor = sf_row["SEASONALITY"].values[0]

        next_value = current_value * prediction_factor * seasonality_factor
        if cap_at_one:
            next_value = min(next_value, 1.0)

        row = {
            "COUNTRY": country,
            "COHORT_MONTH": cohort_month,
            "LT": next_lt,
            "LT_MONTH": next_lt_month,
            metric_col: next_value,
            "IS_PREDICTED": True,
        }
        row.update(extra_cols)
        rows.append(row)

        current_lt = next_lt
        current_value = next_value
        current_lt_month = next_lt_month

    return rows


# ── CONFIG HELPER ─────────────────────────────────────────────────
def load_config(path="inputs/config.yml"):
    """Loads inputs/config.yml and returns it as a dict."""
    with open(path, "r") as f:
        return yaml.safe_load(f)