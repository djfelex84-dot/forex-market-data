import math
import sqlite3
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from config import (
    STOP_LOSS_ATR_MULTIPLIER,
    TAKE_PROFIT_R_MULTIPLE,
    MAX_TRADE_MINUTES,
    get_instrument_config,
)
from quality_gate import analyze_timeframes, PASS_SCORE


DB_URI = "file:/app/data/v4_history.db?mode=ro"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
SYMBOLS = ("EUR/USD", "GBP/USD")
M30_MINUTES = 30
H1_MINUTES = 60
MIN_BARS = 60
ANALYSIS_WINDOW_M30 = 200
ANALYSIS_WINDOW_H1 = 120
TRAIN_START = datetime(2021, 1, 1)
TRAIN_END = datetime(2025, 1, 1)
HOLDOUT_START = datetime(2026, 1, 1)
READ_OPEN_LIMIT = HOLDOUT_START - timedelta(minutes=M30_MINUTES)
PROGRESS_EVERY = 5000


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
    return result


def build_h1_from_m30(rows):
    by_time = {row["_time"]: row for row in rows}
    h1 = []

    for open_time in sorted(by_time):
        if open_time.minute != 0:
            continue

        second_time = open_time + timedelta(minutes=M30_MINUTES)
        first = by_time.get(open_time)
        second = by_time.get(second_time)

        if first is None or second is None:
            continue

        h1.append(
            {
                "datetime": open_time.strftime(TIME_FORMAT),
                "_time": open_time,
                "_close_time": open_time + timedelta(minutes=H1_MINUTES),
                "open": first["open"],
                "high": max(first["high"], second["high"]),
                "low": min(first["low"], second["low"]),
                "close": second["close"],
            }
        )

    return h1


def safe_h1_window(h1_rows, h1_close_times, signal_close):
    end = bisect_right(h1_close_times, signal_close)
    start = max(0, end - ANALYSIS_WINDOW_H1)
    return h1_rows[start:end]


def cohort_for_entry(entry_time):
    if entry_time < TRAIN_START:
        return None, None
    if entry_time < TRAIN_END:
        return "TRAIN_2021_2024", TRAIN_END
    if entry_time < HOLDOUT_START:
        return "VALIDATION_2025", HOLDOUT_START
    return None, None


def execute_m30_trade(*, rows, index_by_time, signal_index, signal, atr_value, symbol, split_end):
    instrument = get_instrument_config(symbol)
    pip_size = float(instrument["pip_size"])
    spread_pips = float(instrument["assumed_spread_pips"])
    min_stop_pips = float(instrument["min_stop_pips"])

    signal_row = rows[signal_index]
    entry_price = float(signal_row["close"])
    signal_open = signal_row["_time"]
    entry_time = signal_open + timedelta(minutes=M30_MINUTES)
    deadline = entry_time + timedelta(minutes=MAX_TRADE_MINUTES)

    if deadline >= split_end:
        return {"reason": "BOUNDARY_GUARD", "r": None, "exit_time": None}

    stop_distance = max(
        float(atr_value) * float(STOP_LOSS_ATR_MULTIPLIER),
        min_stop_pips * pip_size,
    )
    target_distance = stop_distance * float(TAKE_PROFIT_R_MULTIPLE)

    if signal == "BUY":
        stop_loss = entry_price - stop_distance
        take_profit = entry_price + target_distance
    else:
        stop_loss = entry_price + stop_distance
        take_profit = entry_price - target_distance

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

        if signal == "BUY":
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
            gross_pips = target_distance / pip_size
            reason = "TAKE_PROFIT"
        elif step == max_bars - 1:
            if signal == "BUY":
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
    m = metrics(records)
    return (
        f"{str(label):24} | N={m['n']:5d} | WR={m['wr']:6.2f}% | "
        f"PF={m['pf']:7.3f} | AvgR={m['avg_r']:+8.3f} | "
        f"NetR={m['net_r']:+9.2f} | DD={m['dd']:8.2f}R"
    )


def quality_band(score):
    score = int(score)
    if score >= 95:
        return "95-100"
    if score >= 90:
        return "90-94"
    if score >= 85:
        return "85-89"
    return "80-84"


def build_records():
    connection = sqlite3.connect(DB_URI, uri=True)
    records = []
    diagnostics = defaultdict(Counter)

    try:
        for symbol in SYMBOLS:
            rows = load_m30(connection, symbol)
            h1_rows = build_h1_from_m30(rows)
            h1_close_times = [row["_close_time"] for row in h1_rows]
            index_by_time = {row["_time"]: i for i, row in enumerate(rows)}
            next_allowed_entry = datetime.min
            symbol_signal_count = 0

            print(f"BUILDING {symbol} | M30={len(rows)} | H1={len(h1_rows)}", flush=True)

            for index in range(MIN_BARS - 1, len(rows)):
                if index > 0 and index % PROGRESS_EVERY == 0:
                    pct = index / max(1, len(rows)) * 100.0
                    print(
                        f"PROGRESS {symbol} | {index}/{len(rows)} "
                        f"({pct:.1f}%) | Signals={symbol_signal_count}",
                        flush=True,
                    )

                row = rows[index]
                signal_open = row["_time"]
                signal_close = signal_open + timedelta(minutes=M30_MINUTES)

                if signal_close >= HOLDOUT_START:
                    break

                period, split_end = cohort_for_entry(signal_close)
                if period is None:
                    continue

                m30_window = rows[max(0, index - ANALYSIS_WINDOW_M30 + 1):index + 1]
                h1_window = safe_h1_window(h1_rows, h1_close_times, signal_close)

                if len(m30_window) < MIN_BARS or len(h1_window) < MIN_BARS:
                    diagnostics[symbol]["NO_DATA"] += 1
                    continue

                result = analyze_timeframes(
                    candles_30m=m30_window,
                    candles_60m=h1_window,
                    symbol=symbol,
                )

                diagnostics[symbol][result.get("status", "UNKNOWN")] += 1
                for blocker in result.get("blockers", []):
                    diagnostics[symbol][f"BLOCK::{blocker}"] += 1

                signal = result.get("signal")
                if result.get("status") != "VALID" or signal not in ("BUY", "SELL"):
                    continue

                if signal_close < next_allowed_entry:
                    diagnostics[symbol]["SKIP_OPEN_TRADE"] += 1
                    continue

                trade = execute_m30_trade(
                    rows=rows,
                    index_by_time=index_by_time,
                    signal_index=index,
                    signal=signal,
                    atr_value=result["primary_atr"],
                    symbol=symbol,
                    split_end=split_end,
                )

                if trade.get("exit_time") is not None:
                    next_allowed_entry = trade["exit_time"]

                records.append(
                    {
                        "symbol": symbol,
                        "period": period,
                        "year": signal_close.year,
                        "signal": signal,
                        "signal_time": signal_close,
                        "quality_score": int(result["quality_score"]),
                        "quality_band": quality_band(result["quality_score"]),
                        "reason": trade["reason"],
                        "r": trade.get("r"),
                    }
                )
                symbol_signal_count += 1

            print(
                f"DONE {symbol} | Signals={symbol_signal_count}",
                flush=True,
            )
    finally:
        connection.close()

    return records, diagnostics


def period_rows(records, period):
    return [row for row in records if row["period"] == period]


def print_group(records, title, key):
    print("\n" + "=" * 118)
    print(title)
    print("=" * 118)

    for period in ("TRAIN_2021_2024", "VALIDATION_2025"):
        print(f"\n{period}")
        subset = period_rows(records, period)
        values = sorted({row[key] for row in subset}, key=str)
        if not values:
            print("NONE")
            continue
        for value in values:
            print(metric_line(value, [row for row in subset if row[key] == value]))


def main():
    print("=" * 118)
    print("AS QUALITY GATE BACKTEST | NEW M30/H1 SETUP LOGIC")
    print("=" * 118)
    print("Research only | DB opened read-only | No Telegram | No API | No live writes")
    print(f"PASS_SCORE={PASS_SCORE} | TRAIN=2021-2024 | VALIDATION=2025 | 2026 NOT READ")
    print("Execution: conservative M30 replay | same SL/TP/cost config | one open trade per symbol")

    records, diagnostics = build_records()

    train = period_rows(records, "TRAIN_2021_2024")
    validation = period_rows(records, "VALIDATION_2025")

    print("\n" + "=" * 118)
    print("PRIMARY RESULT")
    print("=" * 118)
    print(metric_line("TRAIN COMBINED", train))
    print(metric_line("2025 COMBINED", validation))

    print_group(records, "BY SYMBOL", "symbol")
    print_group(records, "BUY vs SELL", "signal")
    print_group(records, "BY QUALITY SCORE BAND", "quality_band")

    print("\n" + "=" * 118)
    print("YEAR-BY-YEAR")
    print("=" * 118)
    for year in (2021, 2022, 2023, 2024, 2025):
        print(metric_line(str(year), [row for row in records if row["year"] == year]))

    print("\n" + "=" * 118)
    print("DIAGNOSTICS | TOP REJECTIONS")
    print("=" * 118)
    for symbol in SYMBOLS:
        print(f"\n{symbol}")
        for key, count in diagnostics[symbol].most_common(12):
            print(f"{key:46} | {count}")

    train_m = metrics(train)
    validation_m = metrics(validation)
    enough_sample = train_m["n"] >= 100 and validation_m["n"] >= 25
    positive_both = (
        train_m["pf"] > 1.0
        and train_m["avg_r"] > 0.0
        and validation_m["pf"] > 1.0
        and validation_m["avg_r"] > 0.0
    )

    print("\n" + "=" * 118)
    print("DECISION")
    print("=" * 118)
    if enough_sample and positive_both:
        print("CANDIDATE PASSES FIRST SCREEN: positive TRAIN and 2025 with minimum sample.")
        print("NEXT: replay qualifying signals on 5m execution and then run live shadow collection.")
    elif not enough_sample:
        print("INCONCLUSIVE: too few trades for a stable first decision.")
        print("NEXT: inspect which structural blocker is suppressing setups before changing thresholds.")
    else:
        print("CANDIDATE FAILS FIRST SCREEN: no positive repeatability across TRAIN and 2025.")
        print("NEXT: redesign setup trigger itself; do not tune RSI/hour/EMA bins around this result.")

    print("\nQUALITY_GATE_BACKTEST_OK")


if __name__ == "__main__":
    main()
