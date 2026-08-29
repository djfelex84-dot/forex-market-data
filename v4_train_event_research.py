"""Run locked V4 TRAIN research on the verified Dukascopy M1 dataset.

The module refuses partial builds and never reads 2025.  M30 signal candles
are derived from observed BID/ASK M1 quotes while audited provider fillers are
used only to prove source-grid completeness.  Trade execution is simulated on
the exact M1 BID/ASK sides, so spread is embedded rather than subtracted as a
fixed estimate.
"""

import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import v4_dukascopy_train_builder as train_builder
import v4_research_data as research_data
from v4_event_strategy import (
    SETUP_BREAKOUT_RETEST,
    SETUP_FAKEOUT,
    TRAIN_END,
    TRAIN_START,
    generate_v4_events,
)


DATABASE_PATH = Path("/tmp/v4_dukascopy_train/v4_train_m1.sqlite3")
MANIFEST_PATH = Path("/tmp/v4_dukascopy_train/manifest.json")
RESULT_PATH = Path("/tmp/v4_dukascopy_train/train_event_research.json")
SYMBOLS = ("EUR/USD", "GBP/USD")
SETUPS = (SETUP_BREAKOUT_RETEST, SETUP_FAKEOUT)
TAKE_PROFIT_R_MULTIPLE = 1.5
MAX_TRADE_MINUTES = 180
INSTRUMENTS = {
    "EUR/USD": {"pip_size": 0.0001, "min_stop_pips": 5.0},
    "GBP/USD": {"pip_size": 0.0001, "min_stop_pips": 5.0},
}


def _load_manifest(path=MANIFEST_PATH):
    path = Path(path)
    if not path.is_file():
        raise RuntimeError(f"Complete TRAIN manifest is missing: {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Invalid TRAIN manifest: {path}") from error

    required = {
        "status": "COMPLETE",
        "validation_2025_locked": True,
        "context_start_utc": "2020-12-01 00:00:00",
        "train_start_utc": "2021-01-01 00:00:00",
        "end_exclusive_utc": "2025-01-01 00:00:00",
        "adapter_gate_commit": train_builder.ADAPTER_GATE_COMMIT,
        "adapter_gate_manifest_sha256": (
            train_builder.ADAPTER_GATE_MANIFEST_SHA256
        ),
        "production_database_opened": False,
        "production_database_changed": False,
    }
    for key, expected in required.items():
        if manifest.get(key) != expected:
            raise RuntimeError(
                f"TRAIN manifest gate failed for {key}: "
                f"{manifest.get(key)!r} != {expected!r}"
            )
    if not manifest.get("database_sha256"):
        raise RuntimeError("TRAIN manifest has no final database SHA256")
    return manifest


def open_complete_train_database(
    database_path=DATABASE_PATH,
    manifest_path=MANIFEST_PATH,
):
    database_path = Path(database_path)
    manifest = _load_manifest(manifest_path)
    if Path(manifest.get("database_path", "")) != database_path:
        raise RuntimeError(
            f"TRAIN database path mismatch: {manifest.get('database_path')} "
            f"!= {database_path}"
        )
    if not database_path.is_file():
        raise RuntimeError(f"TRAIN database is missing: {database_path}")
    actual_hash = research_data.sha256_file(database_path)
    if actual_hash != manifest["database_sha256"]:
        raise RuntimeError(
            f"TRAIN database SHA256 mismatch: {actual_hash} != "
            f"{manifest['database_sha256']}"
        )

    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"TRAIN database integrity failure: {integrity}")
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        for key, expected in train_builder._metadata_values().items():
            if metadata.get(key) != expected:
                raise RuntimeError(
                    f"TRAIN database metadata mismatch for {key}: "
                    f"{metadata.get(key)!r} != {expected!r}"
                )
        expected_keys = {
            (symbol, day.strftime("%Y-%m-%d"))
            for symbol in SYMBOLS
            for day in train_builder.research_days()
        }
        missing = train_builder.validate_database(connection, expected_keys)
        if missing:
            raise RuntimeError(f"TRAIN database is incomplete: {missing[:5]}")
    except Exception:
        connection.close()
        raise
    return connection, manifest


def _observed_row(sql_row):
    timestamp = research_data.parse_utc(sql_row[0])
    values = iter(sql_row[1:13])
    sides = {}
    for side in research_data.SIDES:
        sides[side] = {
            field: float(next(values))
            for field in research_data.OHLC_FIELDS
        }
    return {
        "timestamp": timestamp,
        **sides,
        "bid_volume": float(sql_row[13]),
        "ask_volume": float(sql_row[14]),
        "quality_status": str(sql_row[15]),
        "source_bar_count": 2,
        "source_observed": True,
    }


def _filler_row(sql_row):
    timestamp = research_data.parse_utc(sql_row[0])
    bid = float(sql_row[1])
    ask = float(sql_row[2])
    mid = (bid + ask) / 2.0
    return {
        "timestamp": timestamp,
        "bid": {field: bid for field in research_data.OHLC_FIELDS},
        "ask": {field: ask for field in research_data.OHLC_FIELDS},
        "mid": {field: mid for field in research_data.OHLC_FIELDS},
        "bid_volume": 0.0,
        "ask_volume": 0.0,
        "quality_status": str(sql_row[3]),
        "source_bar_count": 2,
        "source_observed": False,
    }


def _bar_cursor(connection, symbol, start, end):
    return connection.execute(
        """
        SELECT datetime,
               bid_open, bid_high, bid_low, bid_close,
               ask_open, ask_high, ask_low, ask_close,
               mid_open, mid_high, mid_low, mid_close,
               bid_volume, ask_volume, quality_status
        FROM m1_bars
        WHERE symbol = ? AND datetime >= ? AND datetime < ?
        ORDER BY datetime
        """,
        (
            symbol,
            start.strftime(research_data.TIME_FORMAT),
            end.strftime(research_data.TIME_FORMAT),
        ),
    )


def _gap_cursor(connection, symbol, start, end):
    return connection.execute(
        """
        SELECT datetime, bid_open, ask_open, reason
        FROM m1_gaps
        WHERE symbol = ? AND datetime >= ? AND datetime < ?
        ORDER BY datetime
        """,
        (
            symbol,
            start.strftime(research_data.TIME_FORMAT),
            end.strftime(research_data.TIME_FORMAT),
        ),
    )


def iter_source_grid(connection, symbol, start, end):
    if symbol not in SYMBOLS:
        raise ValueError(f"Unsupported TRAIN symbol: {symbol}")
    start = research_data.parse_utc(start)
    end = research_data.parse_utc(end)
    if not train_builder.CONTEXT_START <= start < end <= TRAIN_END:
        raise RuntimeError(f"Attempted TRAIN grid read outside lock: {start}..{end}")

    bars = iter(_bar_cursor(connection, symbol, start, end))
    gaps = iter(_gap_cursor(connection, symbol, start, end))
    bar = next(bars, None)
    gap = next(gaps, None)
    previous = None

    while bar is not None or gap is not None:
        if bar is not None and gap is not None and bar[0] == gap[0]:
            raise RuntimeError(f"Observed/filler overlap: {symbol} {bar[0]}")
        if gap is None or (bar is not None and bar[0] < gap[0]):
            row = _observed_row(bar)
            bar = next(bars, None)
        else:
            row = _filler_row(gap)
            gap = next(gaps, None)
        timestamp = row["timestamp"]
        if previous is not None and timestamp <= previous:
            raise RuntimeError(f"Duplicate/out-of-order source grid: {timestamp}")
        previous = timestamp
        yield row


def load_verified_m30(connection, symbol):
    m30_rows = []
    quality = Counter()
    bucket_rows = []
    current_bucket = None

    def flush():
        if not bucket_rows:
            return
        aggregated = research_data.aggregate_verified_grid_to_m30(bucket_rows)
        if len(aggregated) != 1:
            raise RuntimeError("One M30 source bucket produced multiple outputs")
        row = aggregated[0]
        quality[row["quality_status"]] += 1
        if row["quality_status"] == "MISSING_SOURCE_GRID":
            raise RuntimeError(
                f"Incomplete M30 source grid: {symbol} {row['timestamp']}"
            )
        m30_rows.append(row)

    for row in iter_source_grid(
        connection,
        symbol,
        train_builder.CONTEXT_START,
        TRAIN_END,
    ):
        bucket = research_data.floor_m30(row["timestamp"])
        if current_bucket is None:
            current_bucket = bucket
        if bucket != current_bucket:
            flush()
            bucket_rows.clear()
            current_bucket = bucket
        bucket_rows.append(row)
    flush()
    return m30_rows, quality


def load_grid_window(connection, symbol, start, minutes=MAX_TRADE_MINUTES):
    start = research_data.parse_utc(start)
    end = start + timedelta(minutes=minutes)
    if end > TRAIN_END:
        return []
    rows = list(iter_source_grid(connection, symbol, start, end))
    research_data.validate_m1_rows(rows)
    return rows


def load_first_observed_quote(connection, symbol, start, end):
    """Return the first executable quote at or after ``start`` before ``end``."""
    start = research_data.parse_utc(start)
    end = research_data.parse_utc(end)
    if not train_builder.CONTEXT_START <= start < end <= TRAIN_END:
        raise RuntimeError(
            f"Attempted delayed-exit read outside TRAIN lock: {start}..{end}"
        )
    row = _bar_cursor(connection, symbol, start, end).fetchone()
    return None if row is None else _observed_row(row)


def _trade_result(reason, **values):
    return {"reason": reason, "r": None, "exit_time": None, **values}


def execute_trade_m1(*, connection, symbol, event, split_end=TRAIN_END):
    if symbol not in INSTRUMENTS:
        raise ValueError(f"Unknown V4 instrument: {symbol}")
    direction = event["direction"]
    if direction not in ("BUY", "SELL"):
        raise RuntimeError(f"Invalid trade direction: {direction}")
    entry_time = research_data.parse_utc(event["signal_time"])
    split_end = research_data.parse_utc(split_end)
    deadline = entry_time + timedelta(minutes=MAX_TRADE_MINUTES)
    if deadline >= split_end:
        return _trade_result("BOUNDARY_GUARD")

    path = load_grid_window(
        connection,
        symbol,
        entry_time,
        MAX_TRADE_MINUTES,
    )
    expected = [
        entry_time + timedelta(minutes=offset)
        for offset in range(MAX_TRADE_MINUTES)
    ]
    actual = [research_data.parse_utc(row["timestamp"]) for row in path]
    if actual != expected:
        return _trade_result("DATA_GAP")
    if not path[0].get("source_observed", True):
        return _trade_result("NO_ENTRY_QUOTE")

    config = INSTRUMENTS[symbol]
    pip_size = float(config["pip_size"])
    min_stop = float(config["min_stop_pips"]) * pip_size
    raw_stop = float(event["stop_price"])
    entry_bid = float(path[0]["bid"]["open"])
    entry_ask = float(path[0]["ask"]["open"])
    entry_price = entry_ask if direction == "BUY" else entry_bid
    raw_distance = (
        entry_price - raw_stop
        if direction == "BUY"
        else raw_stop - entry_price
    )
    if not math.isfinite(raw_distance) or raw_distance <= 0:
        return _trade_result("INVALID_STOP")
    risk = max(raw_distance, min_stop)
    if direction == "BUY":
        stop_loss = entry_price - risk
        take_profit = entry_price + risk * TAKE_PROFIT_R_MULTIPLE
        execution_side = "bid"
    else:
        stop_loss = entry_price + risk
        take_profit = entry_price - risk * TAKE_PROFIT_R_MULTIPLE
        execution_side = "ask"

    common = {
        "entry_time": entry_time,
        "entry_price": entry_price,
        "entry_bid": entry_bid,
        "entry_ask": entry_ask,
        "entry_spread_pips": (entry_ask - entry_bid) / pip_size,
        "risk_pips": risk / pip_size,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "execution_side": execution_side,
    }
    observed_path = []
    for row in path:
        if not row.get("source_observed", True):
            continue
        observed_path.append(row)
        side = row[execution_side]
        open_price = float(side["open"])
        high = float(side["high"])
        low = float(side["low"])
        exit_time = row["timestamp"] + timedelta(minutes=1)

        if direction == "BUY":
            if open_price <= stop_loss:
                return {
                    **_trade_result("STOP_GAP"),
                    **common,
                    "r": (open_price - entry_price) / risk,
                    "exit_time": exit_time,
                    "exit_price": open_price,
                }
            if open_price >= take_profit:
                return {
                    **_trade_result("TAKE_PROFIT_GAP"),
                    **common,
                    "r": TAKE_PROFIT_R_MULTIPLE,
                    "exit_time": exit_time,
                    "exit_price": take_profit,
                }
            stop_hit = low <= stop_loss
            target_hit = high >= take_profit
        else:
            if open_price >= stop_loss:
                return {
                    **_trade_result("STOP_GAP"),
                    **common,
                    "r": (entry_price - open_price) / risk,
                    "exit_time": exit_time,
                    "exit_price": open_price,
                }
            if open_price <= take_profit:
                return {
                    **_trade_result("TAKE_PROFIT_GAP"),
                    **common,
                    "r": TAKE_PROFIT_R_MULTIPLE,
                    "exit_time": exit_time,
                    "exit_price": take_profit,
                }
            stop_hit = high >= stop_loss
            target_hit = low <= take_profit

        if stop_hit and target_hit:
            reason = "AMBIGUOUS_M1_WORST_SL"
            r_value = -1.0
            exit_price = stop_loss
        elif stop_hit:
            reason = "STOP_LOSS"
            r_value = -1.0
            exit_price = stop_loss
        elif target_hit:
            reason = "TAKE_PROFIT"
            r_value = TAKE_PROFIT_R_MULTIPLE
            exit_price = take_profit
        else:
            continue
        return {
            **_trade_result(reason),
            **common,
            "r": r_value,
            "exit_time": exit_time,
            "exit_price": exit_price,
        }

    if not observed_path:
        return _trade_result("NO_EXIT_QUOTES", **common)
    if not path[-1].get("source_observed", True):
        delayed = load_first_observed_quote(
            connection,
            symbol,
            deadline,
            split_end,
        )
        if delayed is None:
            return _trade_result(
                "NO_TIMEOUT_QUOTE_BEFORE_BOUNDARY",
                **common,
                last_observed_quote_time=observed_path[-1]["timestamp"],
            )
        exit_price = float(delayed[execution_side]["open"])
        r_value = (
            (exit_price - entry_price) / risk
            if direction == "BUY"
            else (entry_price - exit_price) / risk
        )
        return {
            **_trade_result("TIMEOUT_DELAYED_TO_NEXT_QUOTE"),
            **common,
            "r": r_value,
            "exit_time": delayed["timestamp"],
            "exit_quote_time": delayed["timestamp"],
            "exit_price": exit_price,
        }
    last = observed_path[-1]
    exit_price = float(last[execution_side]["close"])
    r_value = (
        (exit_price - entry_price) / risk
        if direction == "BUY"
        else (entry_price - exit_price) / risk
    )
    return {
        **_trade_result("TIMEOUT"),
        **common,
        "r": r_value,
        "exit_time": deadline,
        "exit_quote_time": last["timestamp"],
        "exit_price": exit_price,
    }


def metrics(records):
    evaluated = [row for row in records if row.get("r") is not None]
    evaluated.sort(
        key=lambda row: (
            row.get("exit_time", row["signal_time"]),
            row["symbol"],
            row["signal_time"],
        )
    )
    if not evaluated:
        return {
            "n": 0,
            "wr": 0.0,
            "pf": 0.0,
            "avg_r": 0.0,
            "net_r": 0.0,
            "dd": 0.0,
        }
    values = [float(row["r"]) for row in evaluated]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_loss = abs(sum(losses))
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "n": len(values),
        "wr": len(wins) / len(values) * 100.0,
        "pf": sum(wins) / gross_loss if gross_loss else 999.0,
        "avg_r": sum(values) / len(values),
        "net_r": sum(values),
        "dd": drawdown,
    }


def subset(records, *, setup=None, symbol=None, direction=None, year=None):
    result = records
    if setup is not None:
        result = [row for row in result if row["setup"] == setup]
    if symbol is not None:
        result = [row for row in result if row["symbol"] == symbol]
    if direction is not None:
        result = [row for row in result if row["direction"] == direction]
    if year is not None:
        result = [row for row in result if row["year"] == year]
    return result


def result_summary(records, raw_events):
    summary = {}
    for setup in SETUPS:
        setup_records = subset(records, setup=setup)
        setup_events = subset(raw_events, setup=setup)
        summary[setup] = {
            "raw_confirmed_events": len(setup_events),
            "raw_events_by_symbol": {
                symbol: len(subset(setup_events, symbol=symbol))
                for symbol in SYMBOLS
            },
            "raw_events_by_direction": {
                direction: len(subset(setup_events, direction=direction))
                for direction in ("BUY", "SELL")
            },
            "raw_events_by_symbol_direction": {
                f"{symbol}::{direction}": len(
                    subset(
                        setup_events,
                        symbol=symbol,
                        direction=direction,
                    )
                )
                for symbol in SYMBOLS
                for direction in ("BUY", "SELL")
            },
            "raw_events_by_year": {
                str(year): len(subset(setup_events, year=year))
                for year in range(2021, 2025)
            },
            "train_combined": metrics(setup_records),
            "by_symbol": {
                symbol: metrics(subset(setup_records, symbol=symbol))
                for symbol in SYMBOLS
            },
            "by_direction": {
                direction: metrics(
                    subset(setup_records, direction=direction)
                )
                for direction in ("BUY", "SELL")
            },
            "by_symbol_direction": {
                f"{symbol}::{direction}": metrics(
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
                str(year): metrics(subset(setup_records, year=year))
                for year in range(2021, 2025)
            },
            "execution_reasons": dict(
                sorted(Counter(row["reason"] for row in setup_records).items())
            ),
        }
    return summary


def build_records(connection):
    records = []
    raw_events = []
    diagnostics = defaultdict(lambda: defaultdict(Counter))
    m30_quality = {}

    for symbol in SYMBOLS:
        m30_rows, quality = load_verified_m30(connection, symbol)
        m30_quality[symbol] = dict(sorted(quality.items()))
        strategy_rows = research_data.m30_strategy_rows(m30_rows, side="mid")
        scan = generate_v4_events(strategy_rows)
        for setup in SETUPS:
            diagnostics[symbol][setup].update(scan["diagnostics"][setup])
            next_allowed_entry = datetime.min
            for event in scan[setup]:
                signal_time = research_data.parse_utc(event["signal_time"])
                if not TRAIN_START <= signal_time < TRAIN_END:
                    continue
                raw_events.append(
                    {
                        "symbol": symbol,
                        "setup": setup,
                        "direction": event["direction"],
                        "signal_time": signal_time,
                        "year": signal_time.year,
                    }
                )
                if signal_time < next_allowed_entry:
                    diagnostics[symbol][setup]["SKIP_OPEN_TRADE"] += 1
                    continue
                trade = execute_trade_m1(
                    connection=connection,
                    symbol=symbol,
                    event=event,
                )
                if trade.get("exit_time") is not None:
                    next_allowed_entry = trade["exit_time"]
                elif trade.get("entry_time") is not None:
                    next_allowed_entry = TRAIN_END
                    diagnostics[symbol][setup][
                        "UNRESOLVED_OPEN_TRADE_TO_BOUNDARY"
                    ] += 1
                records.append(
                    {
                        "symbol": symbol,
                        "setup": setup,
                        "direction": event["direction"],
                        "signal_time": signal_time,
                        "year": signal_time.year,
                        "signal_mid_close": event["entry_price"],
                        "structural_stop_candidate": event["stop_price"],
                        **trade,
                    }
                )
    return records, raw_events, diagnostics, m30_quality


def metric_line(label, records):
    value = metrics(records)
    return (
        f"{label:28} | N={value['n']:5d} | WR={value['wr']:6.2f}% | "
        f"PF={value['pf']:7.3f} | AvgR={value['avg_r']:+8.3f} | "
        f"NetR={value['net_r']:+9.2f} | DD={value['dd']:8.2f}R"
    )


def print_results(records, raw_events):
    for setup in SETUPS:
        print("\n" + "=" * 118)
        print(setup)
        print("=" * 118)
        setup_records = subset(records, setup=setup)
        setup_events = subset(raw_events, setup=setup)
        print(f"RAW_CONFIRMED_EVENTS={len(setup_events)}")
        print(metric_line("TRAIN COMBINED", setup_records))
        print("BY SYMBOL")
        for symbol in SYMBOLS:
            print(
                f"RAW {symbol:23} | "
                f"Events={len(subset(setup_events, symbol=symbol)):5d}"
            )
            print(metric_line(symbol, subset(setup_records, symbol=symbol)))
        print("BUY vs SELL")
        for direction in ("BUY", "SELL"):
            print(
                f"RAW {direction:23} | "
                f"Events={len(subset(setup_events, direction=direction)):5d}"
            )
            print(
                metric_line(
                    direction,
                    subset(setup_records, direction=direction),
                )
            )
        print("SYMBOL + DIRECTION")
        for symbol in SYMBOLS:
            for direction in ("BUY", "SELL"):
                print(
                    f"RAW {symbol + ' ' + direction:23} | "
                    f"Events={len(subset(setup_events, symbol=symbol, direction=direction)):5d}"
                )
                print(
                    metric_line(
                        f"{symbol} {direction}",
                        subset(
                            setup_records,
                            symbol=symbol,
                            direction=direction,
                        ),
                    )
                )
        print("YEAR-BY-YEAR")
        for year in range(2021, 2025):
            print(
                f"RAW {year:<23} | "
                f"Events={len(subset(setup_events, year=year)):5d}"
            )
            print(metric_line(str(year), subset(setup_records, year=year)))
        reasons = Counter(row["reason"] for row in setup_records)
        print(f"EXECUTION_REASONS={dict(sorted(reasons.items()))}")


def main():
    print("=" * 118)
    print("V4 LOCKED TRAIN EVENT RESEARCH | VERIFIED DUKASCOPY M1 BID/ASK")
    print("=" * 118)
    print("TRAIN=2021-2024 | VALIDATION_2025_LOCKED=True")
    print("M30=observed quotes only | complete audited daily grid required")
    print("Execution=next M30 M1 first quote | actual BID/ASK | spread embedded")
    print("No live/Telegram/production database writes")

    connection, manifest = open_complete_train_database()
    try:
        records, raw_events, diagnostics, m30_quality = build_records(connection)
    finally:
        connection.close()

    print_results(records, raw_events)
    summary = result_summary(records, raw_events)
    payload = {
        "schema_version": 1,
        "research": "V4_LOCKED_TRAIN_EVENT_RESEARCH",
        "train": "2021-2024",
        "validation_2025_locked": True,
        "source_manifest_sha256": research_data.sha256_file(MANIFEST_PATH),
        "source_database_sha256": manifest["database_sha256"],
        "execution_policy": {
            "resolution": "M1",
            "entry": "FIRST_OBSERVED_BID_ASK_QUOTE_AT_NEXT_M30_MINUTE",
            "spread": "EMBEDDED_BID_ASK",
            "structural_stop": True,
            "min_stop_pips": 5.0,
            "target_r": TAKE_PROFIT_R_MULTIPLE,
            "max_trade_minutes": MAX_TRADE_MINUTES,
            "ambiguous_m1": "WORST_CASE_STOP",
        },
        "m30_quality": m30_quality,
        "summary": summary,
        "raw_events": raw_events,
        "records": records,
        "diagnostics": {
            symbol: {
                setup: dict(sorted(values.items()))
                for setup, values in setup_values.items()
            }
            for symbol, setup_values in diagnostics.items()
        },
    }
    digest = research_data.write_json_artifact(RESULT_PATH, payload)
    print(f"\nRESULT={RESULT_PATH}")
    print(f"RESULT_SHA256={digest}")
    print("VALIDATION_2025_LOCKED=True")
    print("V4_LOCKED_TRAIN_EVENT_RESEARCH_OK")


if __name__ == "__main__":
    main()
