import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

from config import (
    STOP_LOSS_ATR_MULTIPLIER,
    TAKE_PROFIT_R_MULTIPLE,
    MAX_TRADE_MINUTES,
    get_instrument_config,
)
from strategy import analyze_market

DB_PATH = "/app/data/v4_history.db"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
SYMBOLS = ("EUR/USD", "GBP/USD")
M30_MINUTES = 30
MIN_WINDOW = 60
ANALYSIS_WINDOW = 199
TRAIN_START = datetime(2021, 1, 1)
TRAIN_END = datetime(2025, 1, 1)
HOLDOUT_START = datetime(2026, 1, 1)
READ_OPEN_LIMIT = HOLDOUT_START - timedelta(minutes=M30_MINUTES)

# Fixed descriptive bins only. They are not optimized and are not production thresholds.
RSI_BINS = (
    (0.0, 32.0, "<32"), (32.0, 40.0, "32-40"),
    (40.0, 48.0, "40-48"), (48.0, 52.0, "48-52"),
    (52.0, 60.0, "52-60"), (60.0, 68.0, "60-68"),
    (68.0, 100.000001, ">=68"),
)
EMA_DISTANCE_ATR_BINS = (
    (0.0, 0.15, "<0.15"), (0.15, 0.30, "0.15-0.30"),
    (0.30, 0.60, "0.30-0.60"), (0.60, 1.00, "0.60-1.00"),
    (1.00, float("inf"), ">=1.00"),
)
SLOPE_STRENGTH_BINS = (
    (0.0, 0.02, "<0.02 ATR"), (0.02, 0.05, "0.02-0.05 ATR"),
    (0.05, 0.10, "0.05-0.10 ATR"), (0.10, 0.20, "0.10-0.20 ATR"),
    (0.20, float("inf"), ">=0.20 ATR"),
)
ATR_PIPS_BINS = (
    (0.0, 3.0, "<3 pips"), (3.0, 5.0, "3-5 pips"),
    (5.0, 8.0, "5-8 pips"), (8.0, 12.0, "8-12 pips"),
    (12.0, 20.0, "12-20 pips"), (20.0, float("inf"), ">=20 pips"),
)
UTC_SESSION_BANDS = (
    (0, 6, "00-05 UTC"), (6, 12, "06-11 UTC"),
    (12, 16, "12-15 UTC"), (16, 21, "16-20 UTC"),
    (21, 24, "21-23 UTC"),
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
    return [
        {"datetime": r[0], "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4])}
        for r in rows
    ]

def state_transition(state, candle_open, direction):
    previous_time = state["last_time"]
    previous_direction = state["last_direction"]
    if previous_time is None:
        state["last_time"] = candle_open
        state["last_direction"] = direction
        return "BASELINE"
    gap_minutes = (candle_open - previous_time).total_seconds() / 60.0
    state["last_time"] = candle_open
    if gap_minutes > M30_MINUTES:
        state["last_direction"] = direction
        return "GAP_BASELINE"
    state["last_direction"] = direction
    if direction is None:
        return "NO_SIGNAL"
    if direction == previous_direction:
        return "CONTINUATION"
    return "NEW_SIGNAL"

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
    signal_open = parse_time(signal_row["datetime"])
    entry_time = signal_open + timedelta(minutes=M30_MINUTES)
    deadline = entry_time + timedelta(minutes=MAX_TRADE_MINUTES)
    if deadline >= split_end:
        return {"reason": "BOUNDARY_GUARD", "r": None, "signal_time": entry_time, "exit_time": None}
    stop_distance = max(float(atr_value) * float(STOP_LOSS_ATR_MULTIPLIER), min_stop_pips * pip_size)
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
            return {"reason": "BOUNDARY_GUARD", "r": None, "signal_time": entry_time, "exit_time": None}
        candle_index = index_by_time.get(candle_open)
        if candle_index is None:
            return {"reason": "DATA_GAP", "r": None, "signal_time": entry_time, "exit_time": None}
        candle = rows[candle_index]
        high, low, close = float(candle["high"]), float(candle["low"]), float(candle["close"])
        if signal == "BUY":
            stop_hit, target_hit = low <= stop_loss, high >= take_profit
        else:
            stop_hit, target_hit = high >= stop_loss, low <= take_profit
        if stop_hit and target_hit:
            gross_pips, reason, exit_price = -risk_pips, "AMBIGUOUS_WORST_SL", stop_loss
        elif stop_hit:
            gross_pips, reason, exit_price = -risk_pips, "STOP_LOSS", stop_loss
        elif target_hit:
            gross_pips, reason, exit_price = target_distance / pip_size, "TAKE_PROFIT", take_profit
        elif step == max_bars - 1:
            gross_pips = (close - entry_price) / pip_size if signal == "BUY" else (entry_price - close) / pip_size
            reason, exit_price = "TIMEOUT", close
        else:
            continue
        net_pips = gross_pips - spread_pips
        return {
            "reason": reason,
            "r": net_pips / risk_pips,
            "signal_time": entry_time,
            "exit_time": candle_close,
            "exit_price": exit_price,
            "net_pips": net_pips,
        }
    raise RuntimeError("execution loop ended without result")

def range_label(value, bins):
    value = float(value)
    for low, high, label in bins:
        if low <= value < high:
            return label
    return "UNCLASSIFIED"

def utc_session_label(hour):
    for start, end, label in UTC_SESSION_BANDS:
        if start <= hour < end:
            return label
    return "UNCLASSIFIED"

def price_position_label(result):
    close = float(result["close"])
    ema_fast = float(result["ema_fast"])
    ema_slow = float(result["ema_slow"])
    if close > ema_fast and close > ema_slow:
        return "ABOVE_BOTH"
    if close < ema_fast and close < ema_slow:
        return "BELOW_BOTH"
    return "BETWEEN_EMAS"

def slope_strength(result):
    atr_value = float(result["atr"])
    if atr_value <= 0:
        return 0.0
    fast = abs(float(result["ema_fast_slope"]))
    slow = abs(float(result["ema_slow_slope"]))
    return ((fast + slow) / 2.0) / atr_value

def metrics(records):
    evaluated = [row for row in records if row.get("r") is not None]
    evaluated.sort(key=lambda row: (row["signal_time"], row["symbol"]))
    if not evaluated:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "avg_r": 0.0, "net_r": 0.0, "dd": 0.0}
    r_values = [float(row["r"]) for row in evaluated]
    wins = [v for v in r_values if v > 0]
    losses = [v for v in r_values if v < 0]
    gp, gl = sum(wins), abs(sum(losses))
    pf = gp / gl if gl > 0 else 999.0
    equity = peak = max_dd = 0.0
    for value in r_values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "n": len(evaluated),
        "wr": len(wins) / len(evaluated) * 100.0,
        "pf": pf,
        "avg_r": sum(r_values) / len(r_values),
        "net_r": sum(r_values),
        "dd": max_dd,
    }

def metric_line(label, records):
    m = metrics(records)
    return (
        f"{str(label):24} | N={m['n']:5d} | WR={m['wr']:6.2f}% | PF={m['pf']:7.3f} | "
        f"AvgR={m['avg_r']:+8.3f} | NetR={m['net_r']:+9.2f} | DD={m['dd']:8.2f}R"
    )

def build_current_new_events():
    connection = sqlite3.connect(DB_PATH)
    records = []
    state_counts = defaultdict(lambda: defaultdict(int))
    try:
        for symbol in SYMBOLS:
            rows = load_m30(connection, symbol)
            index_by_time = {parse_time(row["datetime"]): i for i, row in enumerate(rows)}
            state = {"last_time": None, "last_direction": None}
            print(f"BUILDING {symbol} | M30={len(rows)}")
            for index in range(MIN_WINDOW - 1, len(rows)):
                row = rows[index]
                candle_open = parse_time(row["datetime"])
                candle_close = candle_open + timedelta(minutes=M30_MINUTES)
                if candle_close >= HOLDOUT_START:
                    break
                start = max(0, index - ANALYSIS_WINDOW + 1)
                window = rows[start:index + 1]
                if len(window) < MIN_WINDOW:
                    continue
                result = analyze_market(window, symbol)
                direction = (
                    result.get("signal")
                    if result.get("status") == "VALID" and result.get("signal") in ("BUY", "SELL")
                    else None
                )
                action = state_transition(state, candle_open, direction)
                state_counts[symbol][action] += 1
                period, split_end = cohort_for_entry(candle_close)
                if action != "NEW_SIGNAL" or period is None:
                    continue
                trade = execute_m30_trade(
                    rows=rows,
                    index_by_time=index_by_time,
                    signal_index=index,
                    signal=direction,
                    atr_value=result["atr"],
                    symbol=symbol,
                    split_end=split_end,
                )
                instrument = get_instrument_config(symbol)
                pip_size = float(instrument["pip_size"])
                atr_value = float(result["atr"])
                atr_pips = atr_value / pip_size
                slope_norm = slope_strength(result)
                event_time = candle_close
                records.append({
                    "symbol": symbol,
                    "period": period,
                    "year": event_time.year,
                    "signal": direction,
                    "signal_time": event_time,
                    "utc_hour": event_time.hour,
                    "utc_session": utc_session_label(event_time.hour),
                    "rsi": float(result["rsi"]),
                    "rsi_range": range_label(result["rsi"], RSI_BINS),
                    "ema_distance_atr": float(result["ema_distance_atr"]),
                    "ema_distance_range": range_label(result["ema_distance_atr"], EMA_DISTANCE_ATR_BINS),
                    "ema_direction": str(result["ema_direction"]),
                    "ema_fast_slope": float(result["ema_fast_slope"]),
                    "ema_slow_slope": float(result["ema_slow_slope"]),
                    "slope_strength_atr": slope_norm,
                    "slope_strength_range": range_label(slope_norm, SLOPE_STRENGTH_BINS),
                    "atr": atr_value,
                    "atr_pips": atr_pips,
                    "atr_regime": range_label(atr_pips, ATR_PIPS_BINS),
                    "price_position": price_position_label(result),
                    "setup_score": int(result["setup_score"]),
                    "reason": trade["reason"],
                    "r": trade.get("r"),
                    "exit_time": trade.get("exit_time"),
                })
    finally:
        connection.close()
    return records, state_counts

def period_rows(records, period):
    return [row for row in records if row["period"] == period]

def group_rows(records, key):
    grouped = defaultdict(list)
    for row in records:
        grouped[row[key]].append(row)
    return grouped

def ordered_values(grouped, preferred=None):
    if preferred is not None:
        existing = set(grouped.keys())
        result = [value for value in preferred if value in existing]
        result.extend(sorted(existing - set(result), key=lambda value: str(value)))
        return result
    return sorted(grouped.keys(), key=lambda value: str(value))

def print_group_section(title, records, key, preferred=None):
    print("\n" + "=" * 118)
    print(title)
    print("=" * 118)
    for period in ("TRAIN_2021_2024", "VALIDATION_2025"):
        subset = period_rows(records, period)
        grouped = group_rows(subset, key)
        print(f"\n{period}")
        if not grouped:
            print("NONE")
            continue
        for value in ordered_values(grouped, preferred):
            print(metric_line(value, grouped[value]))

def print_year_repeatability(records):
    print("\n" + "=" * 118)
    print("YEAR-BY-YEAR | CURRENT NEW EVENTS")
    print("=" * 118)
    for year in (2021, 2022, 2023, 2024, 2025):
        print(metric_line(str(year), [r for r in records if r["year"] == year]))
    print("\nBUY vs SELL BY YEAR")
    for year in (2021, 2022, 2023, 2024, 2025):
        for signal in ("BUY", "SELL"):
            print(metric_line(f"{year} {signal}", [r for r in records if r["year"] == year and r["signal"] == signal]))
    print("\nSYMBOL BY YEAR")
    for year in (2021, 2022, 2023, 2024, 2025):
        for symbol in SYMBOLS:
            print(metric_line(f"{year} {symbol}", [r for r in records if r["year"] == year and r["symbol"] == symbol]))

def signature_value(row, dimensions):
    return tuple(row[key] for key in dimensions)

def print_repeatability_observations(records):
    print("\n" + "=" * 118)
    print("OBSERVATIONAL REPEATABILITY CHECK")
    print("=" * 118)
    print("Descriptive only. No threshold optimization, no Quality Gate, no production decision.")
    print("'Positive in both' means only PF>1, AvgR>0 and NetR>0 in the same predefined bucket.")
    dimensions = (
        ("symbol",), ("signal",), ("utc_session",), ("rsi_range",),
        ("ema_distance_range",), ("slope_strength_range",), ("atr_regime",),
        ("price_position",), ("setup_score",), ("symbol", "signal"),
        ("symbol", "utc_session"), ("signal", "utc_session"),
    )
    train = period_rows(records, "TRAIN_2021_2024")
    validation = period_rows(records, "VALIDATION_2025")
    any_repeatable = False
    for dims in dimensions:
        tg, vg = defaultdict(list), defaultdict(list)
        for row in train:
            tg[signature_value(row, dims)].append(row)
        for row in validation:
            vg[signature_value(row, dims)].append(row)
        positives = []
        for signature in sorted(set(tg) & set(vg), key=lambda value: str(value)):
            tm, vm = metrics(tg[signature]), metrics(vg[signature])
            tpos = tm["n"] > 0 and tm["pf"] > 1 and tm["avg_r"] > 0 and tm["net_r"] > 0
            vpos = vm["n"] > 0 and vm["pf"] > 1 and vm["avg_r"] > 0 and vm["net_r"] > 0
            if tpos and vpos:
                positives.append((signature, tm, vm))
        print(f"\nDIMENSIONS: {' + '.join(dims)}")
        if not positives:
            print("No predefined bucket is positive in both TRAIN and 2025.")
            continue
        any_repeatable = True
        for signature, tm, vm in positives:
            print(
                f"{signature} | TRAIN N={tm['n']} PF={tm['pf']:.3f} AvgR={tm['avg_r']:+.3f} NetR={tm['net_r']:+.2f} | "
                f"2025 N={vm['n']} PF={vm['pf']:.3f} AvgR={vm['avg_r']:+.3f} NetR={vm['net_r']:+.2f}"
            )
    print("\n" + "-" * 118)
    if any_repeatable:
        print(
            "OBSERVATION: at least one predefined subset is positive in both TRAIN and 2025. "
            "This is NOT proof of tradable edge and NOT a production recommendation."
        )
    else:
        print(
            "OBSERVATION: no predefined subset tested here is positive in both TRAIN and 2025. "
            "That points toward base entry/candidate logic rather than M30 blocker strictness."
        )

def main():
    print("=" * 118)
    print("AS M30 ENTRY EDGE AUDIT")
    print("=" * 118)
    print("Purpose: inspect whether CURRENT real NEW events contain stable positive regimes.")
    print("CURRENT blockers are NOT relaxed.")
    print("M30 timeframe, SL/TP and trading costs: unchanged.")
    print("Execution: same conservative M30 geometry; ambiguous intrabar = worst-case SL.")
    print("TRAIN: 2021-2024 | VALIDATION: 2025 | 2026: NOT READ / NOT USED.")
    print("No Telegram | No API | No live DB writes | No production DB writes.")
    print("No parameter optimization | No automatic threshold selection.")
    print("No Quality Gate | No production decision.")
    records, state_counts = build_current_new_events()
    print("\n" + "=" * 118)
    print("BASELINE | CURRENT REAL NEW EVENTS")
    print("=" * 118)
    print(metric_line("TRAIN COMBINED", period_rows(records, "TRAIN_2021_2024")))
    print(metric_line("2025 COMBINED", period_rows(records, "VALIDATION_2025")))
    print("\nSTATE-MACHINE COUNTS")
    for symbol in SYMBOLS:
        c = state_counts[symbol]
        print(f"{symbol:8} | NEW={c['NEW_SIGNAL']:5d} | CONT={c['CONTINUATION']:5d} | WAIT={c['NO_SIGNAL']:5d} | GAP={c['GAP_BASELINE']:4d} | BASE={c['BASELINE']:2d}")
    print_group_section("BY SYMBOL", records, "symbol", preferred=SYMBOLS)
    print_group_section("BUY vs SELL", records, "signal", preferred=("BUY", "SELL"))
    print_year_repeatability(records)
    print_group_section("BY UTC HOUR", records, "utc_hour", preferred=tuple(range(24)))
    print_group_section("BY FIXED UTC SESSION BAND", records, "utc_session", preferred=tuple(label for _, _, label in UTC_SESSION_BANDS))
    print_group_section("BY RSI RANGE", records, "rsi_range", preferred=tuple(label for _, _, label in RSI_BINS))
    print_group_section("BY EMA DISTANCE / ATR RANGE", records, "ema_distance_range", preferred=tuple(label for _, _, label in EMA_DISTANCE_ATR_BINS))
    print_group_section("BY EMA DIRECTION", records, "ema_direction", preferred=("UP", "DOWN", "MIXED"))
    print_group_section("BY EMA SLOPE STRENGTH / ATR", records, "slope_strength_range", preferred=tuple(label for _, _, label in SLOPE_STRENGTH_BINS))
    print_group_section("BY VOLATILITY / ATR PIPS REGIME", records, "atr_regime", preferred=tuple(label for _, _, label in ATR_PIPS_BINS))
    print_group_section("BY PRICE POSITION", records, "price_position", preferred=("ABOVE_BOTH", "BELOW_BOTH", "BETWEEN_EMAS"))
    print_group_section("BY SETUP SCORE", records, "setup_score")
    print_repeatability_observations(records)
    print("\n" + "=" * 118)
    print("INTERPRETATION NOTES")
    print("=" * 118)
    print("1. CURRENT NEW events only. Blocked candidates are excluded; blockers were not relaxed.")
    print("2. SetupScore and price-position may have little/no variation because CURRENT VALID requires all checks to pass.")
    print("3. A positive small bucket is not automatically an edge. Check N, year-by-year, both symbols and BUY/SELL dependence.")
    print("4. This script does not search for best thresholds. It reports fixed descriptive bins.")
    print("5. If no stable positive subset repeats in TRAIN and 2025, base entry/candidate logic is the likely problem.")
    print("\nENTRY_EDGE_AUDIT_OK")

if __name__ == "__main__":
    main()
