"""Describe every locked V4 TRAIN event on the verified Dukascopy M1 grid.

This module is deliberately independent from trade selection and execution.
It preserves overlapping events and measures their subsequent midpoint path
at fixed horizons.  Provider filler rows prove source-grid coverage but never
become quote observations or artificial extrema.
"""

import json
import math
import statistics
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import v4_event_anatomy as anatomy
import v4_research_data as research_data
import v4_train_event_research as train_research
from v4_event_strategy import (
    SETUP_BREAKOUT_RETEST,
    SETUP_FAKEOUT,
    TRAIN_END,
    TRAIN_START,
    generate_v4_events,
)


RESULT_PATH = Path("/tmp/v4_dukascopy_train/train_event_anatomy.json")
SYMBOLS = train_research.SYMBOLS
SETUPS = (SETUP_BREAKOUT_RETEST, SETUP_FAKEOUT)
HORIZONS_MINUTES = (30, 60, 120, 180)
PRIMARY_HORIZON_MINUTES = 180
FIRST_PASSAGE_ATR = (0.25, 0.50, 1.00)
BOOTSTRAP_REPLICATIONS = 2_000
BOOTSTRAP_SEED = 42


def load_event_grid(connection, symbol, start, minutes=PRIMARY_HORIZON_MINUTES):
    start = research_data.parse_utc(start)
    end = start + timedelta(minutes=minutes)
    if end > TRAIN_END:
        return []
    rows = list(
        train_research.iter_source_grid(
            connection,
            symbol,
            start,
            end,
        )
    )
    expected = [
        start + timedelta(minutes=offset)
        for offset in range(minutes)
    ]
    actual = [research_data.parse_utc(row["timestamp"]) for row in rows]
    if actual != expected:
        return []
    research_data.validate_m1_rows(rows)
    return rows


def first_passage_verified(
    grid,
    *,
    direction,
    entry,
    atr,
    threshold_atr,
):
    distance = float(atr) * float(threshold_atr)
    if direction == "BUY":
        favorable_price = entry + distance
        adverse_price = entry - distance
    elif direction == "SELL":
        favorable_price = entry - distance
        adverse_price = entry + distance
    else:
        raise ValueError(f"Unknown direction: {direction}")

    for offset, row in enumerate(grid, start=1):
        if not row.get("source_observed", True):
            continue
        high = float(row["mid"]["high"])
        low = float(row["mid"]["low"])
        if direction == "BUY":
            favorable = high >= favorable_price
            adverse = low <= adverse_price
        else:
            favorable = low <= favorable_price
            adverse = high >= adverse_price

        if favorable and adverse:
            return "AMBIGUOUS_M1", offset
        if favorable:
            return "FAVORABLE", offset
        if adverse:
            return "ADVERSE", offset
    return "NONE", None


def _extreme_minute(grid, *, direction, favorable):
    candidates = []
    for offset, row in enumerate(grid, start=1):
        if not row.get("source_observed", True):
            continue
        if direction == "BUY":
            field = "high" if favorable else "low"
        else:
            field = "low" if favorable else "high"
        candidates.append((float(row["mid"][field]), offset))
    if not candidates:
        raise RuntimeError("No observed quote for event extreme")
    values = [value for value, _offset in candidates]
    target = (
        max(values)
        if (direction == "BUY") == favorable
        else min(values)
    )
    return next(offset for value, offset in candidates if value == target)


def analyze_verified_event(*, connection, symbol, event, split_end=TRAIN_END):
    signal_time = research_data.parse_utc(event["signal_time"])
    direction = event["direction"]
    atr = float(event["atr"])
    maximum_horizon = max(HORIZONS_MINUTES)
    record = {
        "event_id": anatomy.event_id(symbol, event),
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
    if direction not in ("BUY", "SELL") or not math.isfinite(atr) or atr <= 0:
        record["status"] = "INVALID_EVENT"
        return record
    if signal_time + timedelta(minutes=maximum_horizon) >= split_end:
        record["status"] = "BOUNDARY_GUARD"
        return record

    grid = load_event_grid(
        connection,
        symbol,
        signal_time,
        maximum_horizon,
    )
    if len(grid) != maximum_horizon:
        record["status"] = "SOURCE_GRID_GAP"
        return record
    grid = grid[:maximum_horizon]
    if not grid[0].get("source_observed", True):
        record["status"] = "NO_ENTRY_QUOTE"
        return record

    entry = float(grid[0]["mid"]["open"])
    record["event_entry_mid"] = entry
    sign = 1.0 if direction == "BUY" else -1.0

    for horizon in HORIZONS_MINUTES:
        horizon_grid = grid[:horizon]
        observed = [
            row
            for row in horizon_grid
            if row.get("source_observed", True)
        ]
        if not observed:
            raise RuntimeError("Entry quote disappeared from its own horizon")
        endpoint = float(observed[-1]["mid"]["close"])
        high = max(float(row["mid"]["high"]) for row in observed)
        low = min(float(row["mid"]["low"]) for row in observed)
        if direction == "BUY":
            favorable_price = max(0.0, high - entry)
            adverse_price = max(0.0, entry - low)
        else:
            favorable_price = max(0.0, entry - low)
            adverse_price = max(0.0, high - entry)
        last_offset = max(
            offset
            for offset, row in enumerate(horizon_grid, start=1)
            if row.get("source_observed", True)
        )

        record[f"fr_{horizon}m_atr"] = sign * (endpoint - entry) / atr
        record[f"mfe_{horizon}m_atr"] = favorable_price / atr
        record[f"mae_{horizon}m_atr"] = adverse_price / atr
        record[f"time_to_mfe_{horizon}m"] = _extreme_minute(
            horizon_grid,
            direction=direction,
            favorable=True,
        )
        record[f"time_to_mae_{horizon}m"] = _extreme_minute(
            horizon_grid,
            direction=direction,
            favorable=False,
        )
        record[f"observed_quotes_{horizon}m"] = len(observed)
        record[f"filler_minutes_{horizon}m"] = horizon - len(observed)
        record[f"endpoint_quote_age_{horizon}m"] = horizon - last_offset

    for threshold in FIRST_PASSAGE_ATR:
        outcome, minute = first_passage_verified(
            grid,
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


def build_anatomy(connection):
    records = []
    diagnostics = {}
    m30_quality = {}
    for symbol in SYMBOLS:
        m30_rows, quality = train_research.load_verified_m30(
            connection,
            symbol,
        )
        m30_quality[symbol] = dict(sorted(quality.items()))
        strategy_rows = research_data.m30_strategy_rows(m30_rows, side="mid")
        scan = generate_v4_events(strategy_rows)
        diagnostics[symbol] = {
            setup: dict(sorted(scan["diagnostics"][setup].items()))
            for setup in SETUPS
        }
        for setup in SETUPS:
            for event in scan[setup]:
                signal_time = research_data.parse_utc(event["signal_time"])
                if not TRAIN_START <= signal_time < TRAIN_END:
                    continue
                records.append(
                    analyze_verified_event(
                        connection=connection,
                        symbol=symbol,
                        event=event,
                    )
                )
    records.sort(
        key=lambda row: (
            row["signal_time"],
            row["symbol"],
            row["setup"],
            row["direction"],
            row["event_id"],
        )
    )
    return records, diagnostics, m30_quality


def subset(records, *, setup=None, symbol=None, direction=None, year=None):
    result = records
    if setup is not None:
        result = [row for row in result if row["setup"] == setup]
    if symbol is not None:
        result = [row for row in result if row["symbol"] == symbol]
    if direction is not None:
        result = [row for row in result if row["direction"] == direction]
    if year is not None:
        result = [row for row in result if row["signal_time"].year == year]
    return result


def summarize_group(records, horizon=PRIMARY_HORIZON_MINUTES):
    evaluated = [row for row in records if row["status"] == "EVALUATED"]
    result = {
        "events": len(records),
        "evaluated": len(evaluated),
        "unique_event_days": len(
            {row["signal_time"].date() for row in evaluated}
        ),
        "attrition": dict(sorted(Counter(row["status"] for row in records).items())),
    }
    if not evaluated:
        result.update(
            {
                "mean_fr_atr": None,
                "median_fr_atr": None,
                "median_mfe_atr": None,
                "median_mae_atr": None,
                "median_observed_quotes": None,
                "first_passage": {},
            }
        )
        return result

    result.update(
        {
            "mean_fr_atr": statistics.fmean(
                float(row[f"fr_{horizon}m_atr"])
                for row in evaluated
            ),
            "median_fr_atr": statistics.median(
                float(row[f"fr_{horizon}m_atr"])
                for row in evaluated
            ),
            "median_mfe_atr": statistics.median(
                float(row[f"mfe_{horizon}m_atr"])
                for row in evaluated
            ),
            "median_mae_atr": statistics.median(
                float(row[f"mae_{horizon}m_atr"])
                for row in evaluated
            ),
            "median_observed_quotes": statistics.median(
                int(row[f"observed_quotes_{horizon}m"])
                for row in evaluated
            ),
            "first_passage": {
                suffix: dict(
                    sorted(
                        Counter(
                            row[f"first_passage_{suffix}atr"]
                            for row in evaluated
                        ).items()
                    )
                )
                for suffix in ("025", "050", "100")
            },
        }
    )
    return result


def safe_bootstrap(records):
    evaluated = [row for row in records if row["status"] == "EVALUATED"]
    if not evaluated:
        return None
    return anatomy.day_block_bootstrap_mean(
        evaluated,
        field=f"fr_{PRIMARY_HORIZON_MINUTES}m_atr",
        replications=BOOTSTRAP_REPLICATIONS,
        seed=BOOTSTRAP_SEED,
    )


def result_summary(records):
    result = {}
    for setup in SETUPS:
        setup_records = subset(records, setup=setup)
        result[setup] = {
            "combined": summarize_group(setup_records),
            "combined_bootstrap": safe_bootstrap(setup_records),
            "by_symbol": {
                symbol: summarize_group(subset(setup_records, symbol=symbol))
                for symbol in SYMBOLS
            },
            "by_direction": {
                direction: summarize_group(
                    subset(setup_records, direction=direction)
                )
                for direction in ("BUY", "SELL")
            },
            "by_symbol_direction": {
                f"{symbol}::{direction}": summarize_group(
                    subset(
                        setup_records,
                        symbol=symbol,
                        direction=direction,
                    )
                )
                for symbol in SYMBOLS
                for direction in ("BUY", "SELL")
            },
            "by_year": {
                str(year): summarize_group(subset(setup_records, year=year))
                for year in range(2021, 2025)
            },
        }
    return result


def print_results(records):
    for setup in SETUPS:
        print("\n" + "=" * 118)
        print(setup)
        print("=" * 118)
        groups = [("TRAIN COMBINED", subset(records, setup=setup))]
        groups.extend(
            (symbol, subset(records, setup=setup, symbol=symbol))
            for symbol in SYMBOLS
        )
        groups.extend(
            (
                direction,
                subset(records, setup=setup, direction=direction),
            )
            for direction in ("BUY", "SELL")
        )
        groups.extend(
            (
                str(year),
                subset(records, setup=setup, year=year),
            )
            for year in range(2021, 2025)
        )
        for label, group in groups:
            summary = summarize_group(group)
            print(
                f"{label:20} | Events={summary['events']:5d} | "
                f"Evaluated={summary['evaluated']:5d} | "
                f"MeanFR={summary['mean_fr_atr']} | "
                f"MedianMFE={summary['median_mfe_atr']} | "
                f"MedianMAE={summary['median_mae_atr']}"
            )


def main():
    print("=" * 118)
    print("V4 LOCKED TRAIN EVENT ANATOMY | VERIFIED DUKASCOPY M1")
    print("=" * 118)
    print("TRAIN=2021-2024 | VALIDATION_2025_LOCKED=True")
    print("All confirmed events retained | no one-open or TP/SL selection")
    print("Provider fillers prove grid coverage but never become quotes")

    connection, manifest = train_research.open_complete_train_database()
    try:
        records, diagnostics, m30_quality = build_anatomy(connection)
    finally:
        connection.close()

    print_results(records)
    payload = {
        "schema_version": 1,
        "research": "V4_LOCKED_TRAIN_EVENT_ANATOMY",
        "train": "2021-2024",
        "validation_2025_locked": True,
        "source_manifest_sha256": research_data.sha256_file(
            train_research.MANIFEST_PATH
        ),
        "source_database_sha256": manifest["database_sha256"],
        "horizons_minutes": HORIZONS_MINUTES,
        "primary_horizon_minutes": PRIMARY_HORIZON_MINUTES,
        "first_passage_atr": FIRST_PASSAGE_ATR,
        "filler_policy": "SOURCE_GRID_ONLY_NEVER_PRICE_OBSERVATION",
        "summary": result_summary(records),
        "m30_quality": m30_quality,
        "diagnostics": diagnostics,
        "records": records,
    }
    digest = research_data.write_json_artifact(RESULT_PATH, payload)
    print(f"\nRESULT={RESULT_PATH}")
    print(f"RESULT_SHA256={digest}")
    print("VALIDATION_2025_LOCKED=True")
    print("V4_LOCKED_TRAIN_EVENT_ANATOMY_OK")


if __name__ == "__main__":
    main()
