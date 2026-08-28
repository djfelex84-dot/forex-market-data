import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from config import (
    MAX_TRADE_MINUTES,
    TAKE_PROFIT_R_MULTIPLE,
    get_instrument_config,
)
from v4_event_strategy import (
    SETUP_BREAKOUT_RETEST,
    SETUP_FAKEOUT,
    generate_v4_events,
)


DB_URI = "file:/app/data/v4_history.db?mode=ro"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
SYMBOLS = ("EUR/USD", "GBP/USD")
M30_MINUTES = 30

TRAIN_START = datetime(2021, 1, 1)
TRAIN_END = datetime(2025, 1, 1)
HOLDOUT_START = datetime(2026, 1, 1)
READ_OPEN_LIMIT = HOLDOUT_START - timedelta(minutes=M30_MINUTES)

SETUPS = (
    SETUP_BREAKOUT_RETEST,
    SETUP_FAKEOUT,
)


def parse_time(value):
    return datetime.strptime(str(value), TIME_FORMAT)


def load_m30(connection, symbol):
    rows = connection.execute(
        """
        SELECT datetime, open, high, low, close
        FROM candles_30m
        WHERE symbol = ? AND datetime < ?
        ORDER BY datetime
        """,
        (symbol, READ_OPEN_LIMIT.strftime(TIME_FORMAT)),
    ).fetchall()

    result = []
    for row in rows:
        timestamp = parse_time(row[0])
        result.append(
            {
                "datetime": row[0],
                "_time": timestamp,
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
            }
        )
    validate_rows(result, symbol)
    return result


def validate_schema(connection):
    table = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='candles_30m'"
    ).fetchone()
    if table is None:
        raise RuntimeError("Required table candles_30m is missing")

    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(candles_30m)").fetchall()
    }
    required = {"symbol", "datetime", "open", "high", "low", "close"}
    missing = required - columns
    if missing:
        raise RuntimeError(f"candles_30m is missing columns: {sorted(missing)}")


def validate_rows(rows, symbol):
    if not rows:
        raise RuntimeError(f"No M30 candles found for {symbol}")

    previous_time = None
    for index, row in enumerate(rows):
        timestamp = row["_time"]
        if timestamp >= READ_OPEN_LIMIT:
            raise RuntimeError(f"2026 boundary violation for {symbol}: {timestamp}")
        if timestamp.minute not in (0, 30) or timestamp.second != 0:
            raise RuntimeError(f"Unaligned M30 timestamp for {symbol}: {timestamp}")
        if previous_time is not None and timestamp <= previous_time:
            raise RuntimeError(f"Duplicate/out-of-order M30 timestamp for {symbol}: {timestamp}")

        open_price = row["open"]
        high = row["high"]
        low = row["low"]
        close = row["close"]
        values = (open_price, high, low, close)
        if not all(math.isfinite(value) and value > 0 for value in values):
            raise RuntimeError(f"Invalid price in {symbol} row {index}")
        if high < max(open_price, low, close) or low > min(open_price, high, close):
            raise RuntimeError(f"Invalid OHLC geometry in {symbol} row {index}")
        previous_time = timestamp


def cohort_for_entry(entry_time):
    if entry_time < TRAIN_START:
        return None, None
    if entry_time < TRAIN_END:
        return "TRAIN_2021_2024", TRAIN_END
    if entry_time < HOLDOUT_START:
        return "VALIDATION_2025", HOLDOUT_START
    return None, None


def execute_trade(*, rows, index_by_time, event, symbol, split_end):
    instrument = get_instrument_config(symbol)
    pip_size = float(instrument["pip_size"])
    spread_pips = float(instrument["assumed_spread_pips"])
    min_stop_pips = float(instrument["min_stop_pips"])

    direction = event["direction"]
    entry_price = float(event["entry_price"])
    entry_time = event["signal_time"]
    raw_stop = float(event["stop_price"])
    min_stop_distance = min_stop_pips * pip_size

    if direction == "BUY":
        structural_distance = entry_price - raw_stop
        if not math.isfinite(structural_distance) or structural_distance <= 0:
            return {"reason": "INVALID_STOP", "r": None, "exit_time": None}
        stop_distance = max(structural_distance, min_stop_distance)
        stop_loss = entry_price - stop_distance
        take_profit = entry_price + stop_distance * float(TAKE_PROFIT_R_MULTIPLE)
    else:
        structural_distance = raw_stop - entry_price
        if not math.isfinite(structural_distance) or structural_distance <= 0:
            return {"reason": "INVALID_STOP", "r": None, "exit_time": None}
        stop_distance = max(structural_distance, min_stop_distance)
        stop_loss = entry_price + stop_distance
        take_profit = entry_price - stop_distance * float(TAKE_PROFIT_R_MULTIPLE)

    deadline = entry_time + timedelta(minutes=MAX_TRADE_MINUTES)
    if deadline >= split_end:
        return {"reason": "BOUNDARY_GUARD", "r": None, "exit_time": None}

    risk_pips = stop_distance / pip_size
    max_bars = max(1, math.ceil(MAX_TRADE_MINUTES / M30_MINUTES))

    for step in range(max_bars):
        candle_open = entry_time + timedelta(minutes=M30_MINUTES * step)
        candle_close = candle_open + timedelta(minutes=M30_MINUTES)

        if candle_close >= split_end:
            return {"reason": "BOUNDARY_GUARD", "r": None, "exit_time": None}

        candle_index = index_by_time.get(candle_open)
        if candle_index is None:
            return {"reason": "DATA_GAP", "r": None, "exit_time": None}

        candle = rows[candle_index]
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])

        if direction == "BUY":
            stop_hit = low <= stop_loss
            target_hit = high >= take_profit
        else:
            stop_hit = high >= stop_loss
            target_hit = low <= take_profit

        if stop_hit and target_hit:
            gross_pips = -risk_pips
            reason = "AMBIGUOUS_WORST_SL"
        elif stop_hit:
            gross_pips = -risk_pips
            reason = "STOP_LOSS"
        elif target_hit:
            gross_pips = risk_pips * float(TAKE_PROFIT_R_MULTIPLE)
            reason = "TAKE_PROFIT"
        elif step == max_bars - 1:
            if direction == "BUY":
                gross_pips = (close - entry_price) / pip_size
            else:
                gross_pips = (entry_price - close) / pip_size
            reason = "TIMEOUT"
        else:
            continue

        net_pips = gross_pips - spread_pips
        return {
            "reason": reason,
            "r": net_pips / risk_pips,
            "exit_time": candle_close,
        }

    raise RuntimeError("execution loop ended without result")


def metrics(records):
    evaluated = [row for row in records if row.get("r") is not None]
    evaluated.sort(key=lambda row: (row["signal_time"], row["symbol"]))

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
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else 999.0

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    return {
        "n": len(values),
        "wr": len(wins) / len(values) * 100.0,
        "pf": pf,
        "avg_r": sum(values) / len(values),
        "net_r": sum(values),
        "dd": max_dd,
    }


def metric_line(label, records):
    value = metrics(records)
    return (
        f"{str(label):30} | N={value['n']:5d} | WR={value['wr']:6.2f}% | "
        f"PF={value['pf']:7.3f} | AvgR={value['avg_r']:+8.3f} | "
        f"NetR={value['net_r']:+9.2f} | DD={value['dd']:8.2f}R"
    )


def build_records():
    connection = sqlite3.connect(DB_URI, uri=True)
    connection.execute("PRAGMA query_only = ON")
    records = []
    diagnostics = defaultdict(lambda: defaultdict(Counter))
    raw_events = []

    try:
        validate_schema(connection)
        for symbol in SYMBOLS:
            rows = load_m30(connection, symbol)
            index_by_time = {row["_time"]: index for index, row in enumerate(rows)}

            print(f"SCANNING {symbol} | M30={len(rows)}", flush=True)
            scan = generate_v4_events(rows)

            for setup in SETUPS:
                events = scan[setup]
                diagnostics[symbol][setup].update(scan["diagnostics"][setup])

                print(
                    f"EVENTS {symbol} | {setup} | Raw={len(events)}",
                    flush=True,
                )

                next_allowed_entry = datetime.min

                for event in events:
                    signal_time = event["signal_time"]
                    period, split_end = cohort_for_entry(signal_time)
                    if period is None:
                        continue

                    raw_events.append(
                        {
                            "symbol": symbol,
                            "setup": setup,
                            "period": period,
                            "year": signal_time.year,
                            "direction": event["direction"],
                            "signal_time": signal_time,
                        }
                    )

                    if signal_time < next_allowed_entry:
                        diagnostics[symbol][setup]["SKIP_OPEN_TRADE"] += 1
                        continue

                    trade = execute_trade(
                        rows=rows,
                        index_by_time=index_by_time,
                        event=event,
                        symbol=symbol,
                        split_end=split_end,
                    )

                    if trade.get("exit_time") is not None:
                        next_allowed_entry = trade["exit_time"]

                    records.append(
                        {
                            "symbol": symbol,
                            "setup": setup,
                            "period": period,
                            "year": signal_time.year,
                            "direction": event["direction"],
                            "signal_time": signal_time,
                            "reason": trade["reason"],
                            "r": trade.get("r"),
                        }
                    )

            print(f"DONE {symbol}", flush=True)

    finally:
        connection.close()

    return records, diagnostics, raw_events


def subset(records, *, setup=None, period=None, symbol=None, direction=None, year=None):
    result = records
    if setup is not None:
        result = [row for row in result if row["setup"] == setup]
    if period is not None:
        result = [row for row in result if row["period"] == period]
    if symbol is not None:
        result = [row for row in result if row["symbol"] == symbol]
    if direction is not None:
        result = [row for row in result if row["direction"] == direction]
    if year is not None:
        result = [row for row in result if row["year"] == year]
    return result


def print_setup_result(records, setup):
    print("\n" + "=" * 122)
    print(setup)
    print("=" * 122)

    train = subset(records, setup=setup, period="TRAIN_2021_2024")
    validation = subset(records, setup=setup, period="VALIDATION_2025")

    print(metric_line("TRAIN COMBINED", train))
    print(metric_line("2025 COMBINED", validation))

    print("\nBY SYMBOL")
    for period in ("TRAIN_2021_2024", "VALIDATION_2025"):
        print(period)
        for symbol in SYMBOLS:
            print(metric_line(symbol, subset(records, setup=setup, period=period, symbol=symbol)))

    print("\nBUY vs SELL")
    for period in ("TRAIN_2021_2024", "VALIDATION_2025"):
        print(period)
        for direction in ("BUY", "SELL"):
            print(metric_line(direction, subset(records, setup=setup, period=period, direction=direction)))

    print("\nSYMBOL + DIRECTION")
    for period in ("TRAIN_2021_2024", "VALIDATION_2025"):
        print(period)
        for symbol in SYMBOLS:
            for direction in ("BUY", "SELL"):
                print(
                    metric_line(
                        f"{symbol} {direction}",
                        subset(
                            records,
                            setup=setup,
                            period=period,
                            symbol=symbol,
                            direction=direction,
                        ),
                    )
                )

    print("\nYEAR-BY-YEAR")
    for year in (2021, 2022, 2023, 2024, 2025):
        print(metric_line(str(year), subset(records, setup=setup, year=year)))


def print_repeatability(records):
    print("\n" + "=" * 122)
    print("REPEATABILITY OBSERVATION | NOT A QUALITY GATE")
    print("=" * 122)

    for setup in SETUPS:
        train_m = metrics(subset(records, setup=setup, period="TRAIN_2021_2024"))
        validation_m = metrics(subset(records, setup=setup, period="VALIDATION_2025"))

        positive_both = (
            train_m["n"] > 0
            and validation_m["n"] > 0
            and train_m["pf"] > 1.0
            and train_m["avg_r"] > 0.0
            and validation_m["pf"] > 1.0
            and validation_m["avg_r"] > 0.0
        )

        observation = (
            "POSITIVE IN BOTH PERIODS"
            if positive_both
            else "NOT POSITIVE IN BOTH PERIODS"
        )

        print(
            f"{setup:30} | {observation} | "
            f"TRAIN N={train_m['n']:5d} | 2025 N={validation_m['n']:5d} | "
            f"TRAIN PF={train_m['pf']:.3f} AvgR={train_m['avg_r']:+.3f} | "
            f"2025 PF={validation_m['pf']:.3f} AvgR={validation_m['avg_r']:+.3f}"
        )


def print_raw_events(raw_events):
    print("\n" + "=" * 122)
    print("CONFIRMED SETUP EVENTS | BEFORE ONE-OPEN EXECUTION RULE")
    print("=" * 122)

    for setup in SETUPS:
        print(f"\n{setup}")
        for period in ("TRAIN_2021_2024", "VALIDATION_2025"):
            print(f"  {period}")
            for symbol in SYMBOLS:
                for direction in ("BUY", "SELL"):
                    count = len(
                        subset(
                            raw_events,
                            setup=setup,
                            period=period,
                            symbol=symbol,
                            direction=direction,
                        )
                    )
                    print(f"    {symbol:7} | {direction:4} | Events={count:5d}")

        print("  YEAR-BY-YEAR")
        for year in (2021, 2022, 2023, 2024, 2025):
            print(f"    {year} | Events={len(subset(raw_events, setup=setup, year=year)):5d}")


def main():
    print("=" * 122)
    print("AS V4 EVENT STRATEGY COMPARISON | BREAKOUT-RETEST vs PREVIOUS-DAY FAKEOUT")
    print("=" * 122)
    print("Research only | DB read-only | No Telegram | No API | No live writes")
    print("TRAIN=2021-2024 | VALIDATION=2025 | 2026 NOT READ")
    print(
        "BREAKOUT_RETEST: prior 12 M30 range | break=0.10 ATR | "
        "retest<=4 bars | next-candle confirmation"
    )
    print(
        "FAKEOUT: Previous Day High/Low UTC only | sweep=0.10 ATR | "
        "next-candle confirmation"
    )
    print("No H1/EMA/RSI/session/Asia filter | fixed rules | no parameter selection")
    print(
        f"Execution: structural SL | TP={TAKE_PROFIT_R_MULTIPLE:.2f}R | "
        f"max trade={MAX_TRADE_MINUTES}m | one open trade per setup per symbol"
    )

    records, diagnostics, raw_events = build_records()

    print_raw_events(raw_events)

    for setup in SETUPS:
        print_setup_result(records, setup)

    print("\n" + "=" * 122)
    print("DIAGNOSTICS | TOP EVENT STATES")
    print("=" * 122)
    for symbol in SYMBOLS:
        for setup in SETUPS:
            print(f"\n{symbol} | {setup}")
            for key, count in diagnostics[symbol][setup].most_common(12):
                print(f"{key:44} | {count}")

    print_repeatability(records)
    print("\nV4_EVENT_COMPARISON_OK")


if __name__ == "__main__":
    main()
