"""Price-path anatomy for V4 events, deliberately separate from execution.

The module keeps every qualifying BREAKOUT_RETEST and FAKEOUT event and asks
whether the subsequent midpoint path has directional structure.  It does not
open trades, choose TP/SL, apply spread, or enforce one-open-position rules.
Validation 2025 is locked by default; callers must explicitly opt in later.
"""

import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

import v4_research_data as research_data
from v4_event_strategy import (
    HOLDOUT_START,
    SETUP_BREAKOUT_RETEST,
    SETUP_FAKEOUT,
    TRAIN_END,
    TRAIN_START,
    generate_v4_events,
)


HORIZONS_MINUTES = (30, 60, 120, 180)
PRIMARY_HORIZON_MINUTES = 180
FIRST_PASSAGE_ATR = (0.25, 0.50, 1.00)
SETUPS = (SETUP_BREAKOUT_RETEST, SETUP_FAKEOUT)


def event_id(symbol, event):
    identity = {
        "symbol": symbol,
        "setup": event["setup"],
        "direction": event["direction"],
        "signal_time": research_data.parse_utc(event["signal_time"]).strftime(
            research_data.TIME_FORMAT
        ),
        "level": float(event["level"]),
        "source": str(event["source"]),
    }
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def event_cohort(signal_time, *, unlock_validation=False):
    signal_time = research_data.parse_utc(signal_time)
    if signal_time < TRAIN_START:
        return None, None
    if signal_time < TRAIN_END:
        return "TRAIN_2021_2024", TRAIN_END
    if signal_time < HOLDOUT_START and unlock_validation:
        return "VALIDATION_2025", HOLDOUT_START
    return None, None


def index_m1(rows):
    research_data.validate_m1_rows(rows)
    return {
        research_data.parse_utc(row["timestamp"]): row
        for row in rows
    }


def exact_m1_path(index, start, minutes):
    start = research_data.parse_utc(start)
    result = []
    for offset in range(minutes):
        timestamp = start + timedelta(minutes=offset)
        row = index.get(timestamp)
        if row is None:
            return None
        result.append(row)
    return result


def _first_extreme(path, side, field, target):
    for index, row in enumerate(path):
        if float(row[side][field]) == target:
            return index + 1
    raise RuntimeError("Extreme was not found in its own path")


def first_passage(path, *, direction, entry, atr, threshold_atr):
    distance = float(atr) * float(threshold_atr)
    if direction == "BUY":
        favorable_price = entry + distance
        adverse_price = entry - distance
    elif direction == "SELL":
        favorable_price = entry - distance
        adverse_price = entry + distance
    else:
        raise ValueError(f"Unknown direction: {direction}")

    for index, row in enumerate(path):
        high = float(row["mid"]["high"])
        low = float(row["mid"]["low"])
        if direction == "BUY":
            favorable = high >= favorable_price
            adverse = low <= adverse_price
        else:
            favorable = low <= favorable_price
            adverse = high >= adverse_price

        if favorable and adverse:
            return "AMBIGUOUS_M1", index + 1
        if favorable:
            return "FAVORABLE", index + 1
        if adverse:
            return "ADVERSE", index + 1
    return "NONE", None


def analyze_event(
    *,
    symbol,
    event,
    m1_index,
    split_end,
    horizons=HORIZONS_MINUTES,
):
    signal_time = research_data.parse_utc(event["signal_time"])
    atr = float(event["atr"])
    direction = event["direction"]
    if not math.isfinite(atr) or atr <= 0:
        raise RuntimeError(f"Invalid event ATR at {signal_time}: {atr}")
    if direction not in ("BUY", "SELL"):
        raise RuntimeError(f"Invalid event direction at {signal_time}: {direction}")
    if not horizons or tuple(sorted(set(horizons))) != tuple(horizons):
        raise ValueError("Horizons must be unique and strictly increasing")

    maximum_horizon = max(horizons)
    record = {
        "event_id": event_id(symbol, event),
        "symbol": symbol,
        "setup": event["setup"],
        "direction": direction,
        "signal_time": signal_time,
        "signal_close_price": float(event["entry_price"]),
        "structural_stop_candidate": float(event["stop_price"]),
        "level": float(event["level"]),
        "atr": atr,
        "source": str(event["source"]),
        "status": "PENDING",
    }

    if signal_time + timedelta(minutes=maximum_horizon) > split_end:
        record["status"] = "BOUNDARY_GUARD"
        return record

    full_path = exact_m1_path(m1_index, signal_time, maximum_horizon)
    if full_path is None:
        record["status"] = "INCOMPLETE_M1_PATH"
        return record

    entry = float(full_path[0]["mid"]["open"])
    record["event_entry_mid"] = entry
    sign = 1.0 if direction == "BUY" else -1.0

    for horizon in horizons:
        path = full_path[:horizon]
        endpoint = float(path[-1]["mid"]["close"])
        high = max(float(row["mid"]["high"]) for row in path)
        low = min(float(row["mid"]["low"]) for row in path)

        forward_price = sign * (endpoint - entry)
        if direction == "BUY":
            favorable_price = max(0.0, high - entry)
            adverse_price = max(0.0, entry - low)
            mfe_target = high
            mae_target = low
            mfe_field = "high"
            mae_field = "low"
        else:
            favorable_price = max(0.0, entry - low)
            adverse_price = max(0.0, high - entry)
            mfe_target = low
            mae_target = high
            mfe_field = "low"
            mae_field = "high"

        record[f"fr_{horizon}m_atr"] = forward_price / atr
        record[f"mfe_{horizon}m_atr"] = favorable_price / atr
        record[f"mae_{horizon}m_atr"] = adverse_price / atr
        record[f"time_to_mfe_{horizon}m"] = _first_extreme(
            path,
            "mid",
            mfe_field,
            mfe_target,
        )
        record[f"time_to_mae_{horizon}m"] = _first_extreme(
            path,
            "mid",
            mae_field,
            mae_target,
        )

    for threshold in FIRST_PASSAGE_ATR:
        outcome, minute = first_passage(
            full_path,
            direction=direction,
            entry=entry,
            atr=atr,
            threshold_atr=threshold,
        )
        suffix = str(int(round(threshold * 100))).zfill(3)
        record[f"first_passage_{suffix}atr"] = outcome
        record[f"first_passage_{suffix}atr_minute"] = minute

    record["status"] = "EVALUATED"
    return record


def build_event_anatomy(
    *,
    symbol,
    m30_rows,
    m1_rows,
    unlock_validation=False,
):
    """Scan all qualifying events and preserve each before execution policies."""
    cutoff = HOLDOUT_START if unlock_validation else TRAIN_END
    m30_open_limit = cutoff - timedelta(minutes=30)
    strategy_rows = [
        row
        for row in research_data.m30_strategy_rows(m30_rows, side="mid")
        if row["_time"] < m30_open_limit
    ]
    allowed_m1_rows = [
        row
        for row in m1_rows
        if research_data.parse_utc(row["timestamp"]) < cutoff
    ]
    scan = generate_v4_events(strategy_rows)
    m1_by_time = index_m1(allowed_m1_rows)
    records = []

    for setup in SETUPS:
        for event in scan[setup]:
            cohort, split_end = event_cohort(
                event["signal_time"],
                unlock_validation=unlock_validation,
            )
            if cohort is None:
                continue
            record = analyze_event(
                symbol=symbol,
                event=event,
                m1_index=m1_by_time,
                split_end=split_end,
            )
            record["cohort"] = cohort
            records.append(record)

    records.sort(
        key=lambda row: (
            row["signal_time"],
            row["setup"],
            row["direction"],
            row["event_id"],
        )
    )
    return records, scan["diagnostics"]


def summarize(records, *, horizon=PRIMARY_HORIZON_MINUTES):
    evaluated = [row for row in records if row["status"] == "EVALUATED"]
    attrition = defaultdict(int)
    for row in records:
        attrition[row["status"]] += 1

    if not evaluated:
        return {
            "events": len(records),
            "evaluated": 0,
            "unique_days": 0,
            "mean_fr_atr": None,
            "median_fr_atr": None,
            "median_mfe_atr": None,
            "median_mae_atr": None,
            "attrition": dict(sorted(attrition.items())),
        }

    fr = [float(row[f"fr_{horizon}m_atr"]) for row in evaluated]
    mfe = [float(row[f"mfe_{horizon}m_atr"]) for row in evaluated]
    mae = [float(row[f"mae_{horizon}m_atr"]) for row in evaluated]
    return {
        "events": len(records),
        "evaluated": len(evaluated),
        "unique_days": len({row["signal_time"].date() for row in evaluated}),
        "mean_fr_atr": statistics.fmean(fr),
        "median_fr_atr": statistics.median(fr),
        "median_mfe_atr": statistics.median(mfe),
        "median_mae_atr": statistics.median(mae),
        "attrition": dict(sorted(attrition.items())),
    }


def day_block_bootstrap_mean(
    records,
    *,
    field=f"fr_{PRIMARY_HORIZON_MINUTES}m_atr",
    replications=2_000,
    seed=42,
):
    """Bootstrap whole UTC event-days, keeping within-day dependence intact."""
    if replications <= 0:
        raise ValueError("replications must be positive")
    evaluated = [
        row
        for row in records
        if row["status"] == "EVALUATED" and row.get(field) is not None
    ]
    if not evaluated:
        raise ValueError("No evaluated event records for bootstrap")

    by_day = defaultdict(list)
    for row in evaluated:
        by_day[row["signal_time"].date()].append(float(row[field]))
    days = sorted(by_day)
    rng = random.Random(seed)
    estimates = []

    for _ in range(replications):
        sample = []
        for _ in days:
            sampled_day = rng.choice(days)
            sample.extend(by_day[sampled_day])
        estimates.append(statistics.fmean(sample))

    estimates.sort()

    def percentile(probability):
        position = (len(estimates) - 1) * probability
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return estimates[lower]
        weight = position - lower
        return estimates[lower] * (1.0 - weight) + estimates[upper] * weight

    return {
        "field": field,
        "point": statistics.fmean(float(row[field]) for row in evaluated),
        "ci_90": (percentile(0.05), percentile(0.95)),
        "ci_95": (percentile(0.025), percentile(0.975)),
        "replications": replications,
        "seed": seed,
        "event_days": len(days),
    }
