import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from config import (
    MIN_EMA_DISTANCE_ATR,
    RSI_BUY_MIN,
    RSI_BUY_MAX,
    RSI_SELL_MIN,
    RSI_SELL_MAX,
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

TRAIN_START = datetime(2021, 1, 1, 0, 0, 0)
TRAIN_END = datetime(2025, 1, 1, 0, 0, 0)
HOLDOUT_START = datetime(2026, 1, 1, 0, 0, 0)

# DB timestamps are candle OPEN times.  We do not load any M30 candle
# whose CLOSE reaches the 2026 holdout boundary.
READ_OPEN_LIMIT = HOLDOUT_START - timedelta(minutes=M30_MINUTES)

FILTER_KEYS = (
    "volatility",
    "ema_separation",
    "ema_slope",
    "price_position",
    "rsi",
)

WEIGHTS = {
    "volatility": 10,
    "ema_separation": 20,
    "ema_slope": 20,
    "price_position": 20,
    "rsi": 30,
}

SCENARIOS = (
    ("CURRENT", None),
    ("NO_RSI", "rsi"),
    ("NO_EMA_SEPARATION", "ema_separation"),
    ("NO_EMA_SLOPE", "ema_slope"),
    ("NO_PRICE_POSITION", "price_position"),
    ("NO_VOLATILITY", "volatility"),
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
        {
            "datetime": row[0],
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
        }
        for row in rows
    ]


def infer_checks(result, symbol):
    candidate = result.get("candidate")
    if candidate not in ("BUY", "SELL"):
        return {}

    instrument = get_instrument_config(symbol)
    min_atr = float(instrument["min_atr"])

    close = float(result["close"])
    ema_fast = float(result["ema_fast"])
    ema_slow = float(result["ema_slow"])
    rsi_value = float(result["rsi"])

    checks = {
        "volatility": float(result["atr"]) >= min_atr,
        "ema_separation": (
            float(result["ema_distance_atr"]) >= MIN_EMA_DISTANCE_ATR
        ),
    }

    if candidate == "BUY":
        checks.update(
            {
                "ema_slope": result["ema_direction"] == "UP",
                "price_position": close > ema_fast and close > ema_slow,
                "rsi": RSI_BUY_MIN <= rsi_value <= RSI_BUY_MAX,
            }
        )
    else:
        checks.update(
            {
                "ema_slope": result["ema_direction"] == "DOWN",
                "price_position": close < ema_fast and close < ema_slow,
                "rsi": RSI_SELL_MIN <= rsi_value <= RSI_SELL_MAX,
            }
        )

    return checks


def validate_strategy_contract(result, checks):
    candidate = result.get("candidate")

    if candidate not in ("BUY", "SELL"):
        if result.get("status") != "NO_SETUP":
            raise RuntimeError("strategy contract mismatch: expected NO_SETUP")
        return

    expected_score = sum(
        WEIGHTS[name] for name, passed in checks.items() if passed
    )
    if int(result.get("setup_score", -1)) != expected_score:
        raise RuntimeError(
            "strategy contract mismatch: "
            f"score={result.get('setup_score')} expected={expected_score}"
        )

    all_passed = all(checks.values())
    expected_status = "VALID" if all_passed else "BLOCKED"
    expected_signal = candidate if all_passed else "WAIT"

    if result.get("status") != expected_status:
        raise RuntimeError(
            "strategy contract mismatch: "
            f"status={result.get('status')} expected={expected_status}"
        )

    if result.get("signal") != expected_signal:
        raise RuntimeError(
            "strategy contract mismatch: "
            f"signal={result.get('signal')} expected={expected_signal}"
        )

    failed = [name for name in FILTER_KEYS if not checks[name]]
    if len(result.get("blockers") or []) != len(failed):
        raise RuntimeError(
            "strategy contract mismatch: blocker count differs from failed checks"
        )


def failed_filter_keys(checks):
    return tuple(
        name for name in FILTER_KEYS if name in checks and not checks[name]
    )


def scenario_direction(result, failed_keys, disabled_filter):
    candidate = result.get("candidate")
    if candidate not in ("BUY", "SELL"):
        return None

    if result.get("status") == "VALID":
        return candidate

    if (
        disabled_filter is not None
        and result.get("status") == "BLOCKED"
        and failed_keys == (disabled_filter,)
    ):
        return candidate

    return None


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


def execute_m30_trade(
    *, rows, index_by_time, signal_index, signal, atr_value, symbol, split_end
):
    instrument = get_instrument_config(symbol)
    pip_size = float(instrument["pip_size"])
    spread_pips = float(instrument["assumed_spread_pips"])
    min_stop_pips = float(instrument["min_stop_pips"])

    signal_row = rows[signal_index]
    entry_price = float(signal_row["close"])
    signal_open = parse_time(signal_row["datetime"])
    entry_time = signal_open + timedelta(minutes=M30_MINUTES)
    deadline = entry_time + timedelta(minutes=MAX_TRADE_MINUTES)

    # Full possible trade window must end strictly before the next split.
    if deadline >= split_end:
        return {
            "reason": "BOUNDARY_GUARD",
            "r": None,
            "signal_time": entry_time,
            "exit_time": None,
        }

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
            return {
                "reason": "BOUNDARY_GUARD",
                "r": None,
                "signal_time": entry_time,
                "exit_time": None,
            }

        candle_index = index_by_time.get(candle_open)
        if candle_index is None:
            return {
                "reason": "DATA_GAP",
                "r": None,
                "signal_time": entry_time,
                "exit_time": None,
            }

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
            # Conservative M30 intrabar handling: unknown order = SL.
            gross_pips = -risk_pips
            reason = "AMBIGUOUS_WORST_SL"
            exit_price = stop_loss
        elif stop_hit:
            gross_pips = -risk_pips
            reason = "STOP_LOSS"
            exit_price = stop_loss
        elif target_hit:
            gross_pips = target_distance / pip_size
            reason = "TAKE_PROFIT"
            exit_price = take_profit
        elif step == max_bars - 1:
            if signal == "BUY":
                gross_pips = (close - entry_price) / pip_size
            else:
                gross_pips = (entry_price - close) / pip_size
            reason = "TIMEOUT"
            exit_price = close
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


def metrics(records):
    evaluated = [row for row in records if row.get("r") is not None]
    evaluated.sort(
        key=lambda row: (
            row.get("signal_time"),
            row.get("symbol", ""),
        )
    )
    skipped = len(records) - len(evaluated)

    if not evaluated:
        return {
            "events": len(records),
            "n": 0,
            "skipped": skipped,
            "wr": 0.0,
            "pf": 0.0,
            "avg_r": 0.0,
            "net_r": 0.0,
            "dd": 0.0,
            "ambiguous": 0,
            "relaxed": 0,
        }

    r_values = [float(row["r"]) for row in evaluated]
    wins = [value for value in r_values if value > 0]
    losses = [value for value in r_values if value < 0]

    gp = sum(wins)
    gl = abs(sum(losses))
    pf = gp / gl if gl > 0 else 999.0

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in r_values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    return {
        "events": len(records),
        "n": len(evaluated),
        "skipped": skipped,
        "wr": len(wins) / len(evaluated) * 100.0,
        "pf": pf,
        "avg_r": sum(r_values) / len(r_values),
        "net_r": sum(r_values),
        "dd": max_dd,
        "ambiguous": sum(
            1
            for row in evaluated
            if row.get("reason") == "AMBIGUOUS_WORST_SL"
        ),
        "relaxed": sum(
            1
            for row in evaluated
            if row.get("origin") == "RELAXED_BLOCKED"
        ),
    }


def show(label, records):
    m = metrics(records)
    print(
        f"{label:30} | "
        f"Events={m['events']:4d} | "
        f"N={m['n']:4d} | "
        f"Skip={m['skipped']:3d} | "
        f"Relaxed={m['relaxed']:4d} | "
        f"WR={m['wr']:6.2f}% | "
        f"PF={m['pf']:6.3f} | "
        f"AvgR={m['avg_r']:+7.3f} | "
        f"NetR={m['net_r']:+8.2f} | "
        f"DD={m['dd']:7.2f}R | "
        f"Amb={m['ambiguous']:3d}"
    )


def build_records():
    connection = sqlite3.connect(DB_PATH)
    all_records = defaultdict(list)
    blocked_counts = {
        "TRAIN_2021_2024": Counter(),
        "VALIDATION_2025": Counter(),
    }
    state_counts = defaultdict(Counter)
    contract_checks = 0

    try:
        for symbol in SYMBOLS:
            rows = load_m30(connection, symbol)
            index_by_time = {
                parse_time(row["datetime"]): index
                for index, row in enumerate(rows)
            }
            states = {
                name: {"last_time": None, "last_direction": None}
                for name, _ in SCENARIOS
            }

            print(f"BUILDING {symbol} | M30={len(rows)}")

            for index in range(MIN_WINDOW - 1, len(rows)):
                row = rows[index]
                candle_open = parse_time(row["datetime"])
                candle_close = candle_open + timedelta(minutes=M30_MINUTES)

                if candle_close >= HOLDOUT_START:
                    break

                start = max(0, index - ANALYSIS_WINDOW + 1)
                window = rows[start : index + 1]
                if len(window) < MIN_WINDOW:
                    continue

                result = analyze_market(window, symbol)
                checks = infer_checks(result, symbol)
                validate_strategy_contract(result, checks)
                contract_checks += 1

                failed_keys = failed_filter_keys(checks)
                period, split_end = cohort_for_entry(candle_close)

                if period is not None and result.get("status") == "BLOCKED":
                    blocked_counts[period][
                        (int(result["setup_score"]), failed_keys)
                    ] += 1

                for scenario_name, disabled_filter in SCENARIOS:
                    direction = scenario_direction(
                        result,
                        failed_keys,
                        disabled_filter,
                    )
                    action = state_transition(
                        states[scenario_name],
                        candle_open,
                        direction,
                    )
                    state_counts[(symbol, scenario_name)][action] += 1

                    if action != "NEW_SIGNAL" or period is None:
                        continue

                    origin = (
                        "VALID"
                        if result.get("status") == "VALID"
                        else "RELAXED_BLOCKED"
                    )

                    trade = execute_m30_trade(
                        rows=rows,
                        index_by_time=index_by_time,
                        signal_index=index,
                        signal=direction,
                        atr_value=result["atr"],
                        symbol=symbol,
                        split_end=split_end,
                    )

                    all_records[scenario_name].append(
                        {
                            "scenario": scenario_name,
                            "symbol": symbol,
                            "period": period,
                            "year": candle_close.year,
                            "signal": direction,
                            "signal_time": candle_close,
                            "setup_score": int(result["setup_score"]),
                            "failed_keys": failed_keys,
                            "origin": origin,
                            "reason": trade["reason"],
                            "r": trade.get("r"),
                            "exit_time": trade.get("exit_time"),
                        }
                    )
    finally:
        connection.close()

    return all_records, blocked_counts, state_counts, contract_checks


def period_rows(all_records, scenario, period, symbol=None):
    rows = [
        row
        for row in all_records[scenario]
        if row["period"] == period
    ]
    if symbol is not None:
        rows = [row for row in rows if row["symbol"] == symbol]
    return rows


def print_blocked_counts(blocked_counts):
    print("\n===== RAW BLOCKED OBSERVATIONS =====")
    print("Candle observations only; NOT trade events.")

    for period in ("TRAIN_2021_2024", "VALIDATION_2025"):
        print(f"\n{period}")
        for (score, failed), count in blocked_counts[period].most_common(20):
            failed_text = "+".join(failed) if failed else "NONE"
            print(
                f"Score={score:3d} | "
                f"Failed={failed_text:45} | "
                f"N={count}"
            )


def main():
    print("=" * 118)
    print("AS M30 BLOCKED-OPPORTUNITY AUDIT")
    print("=" * 118)
    print("Purpose: test whether current strategy all() gate is too strict.")
    print("Only M30 gate is audited; H1 is intentionally NOT mixed into this test.")
    print("Strategy thresholds: unchanged.")
    print("Execution: current geometry, evaluated conservatively on M30 history.")
    print("AMBIGUOUS: worst-case STOP LOSS.")
    print("TRAIN: 2021-2024 | VALIDATION: 2025 | 2026: NOT READ / NOT USED.")
    print("No Telegram | No API | No live DB writes.")

    all_records, blocked_counts, state_counts, contract_checks = build_records()

    print(f"\nSTRATEGY_CONTRACT_CHECKS: {contract_checks}")
    print("\n===== MAIN COMPARISON =====")

    for scenario_name, _ in SCENARIOS:
        print(f"\n--- {scenario_name} ---")
        show(
            "TRAIN COMBINED",
            period_rows(all_records, scenario_name, "TRAIN_2021_2024"),
        )
        show(
            "2025 COMBINED",
            period_rows(all_records, scenario_name, "VALIDATION_2025"),
        )
        for symbol in SYMBOLS:
            show(
                "TRAIN " + symbol,
                period_rows(
                    all_records,
                    scenario_name,
                    "TRAIN_2021_2024",
                    symbol,
                ),
            )
            show(
                "2025 " + symbol,
                period_rows(
                    all_records,
                    scenario_name,
                    "VALIDATION_2025",
                    symbol,
                ),
            )

    print("\n===== YEAR-BY-YEAR =====")
    for scenario_name, _ in SCENARIOS:
        print(f"\n{scenario_name}")
        for year in (2021, 2022, 2023, 2024, 2025):
            show(
                str(year),
                [
                    row
                    for row in all_records[scenario_name]
                    if row["year"] == year
                ],
            )

    print_blocked_counts(blocked_counts)

    print("\n===== STATE-MACHINE COUNTS =====")
    for symbol in SYMBOLS:
        for scenario_name, _ in SCENARIOS:
            counts = state_counts[(symbol, scenario_name)]
            print(
                f"{symbol:8} | {scenario_name:20} | "
                f"NEW={counts['NEW_SIGNAL']:4d} | "
                f"CONT={counts['CONTINUATION']:5d} | "
                f"WAIT={counts['NO_SIGNAL']:5d} | "
                f"GAP={counts['GAP_BASELINE']:4d} | "
                f"BASE={counts['BASELINE']:2d}"
            )

    print("\n===== DECISION RULE =====")
    print("Do NOT choose a relaxation from combined PF alone.")
    print(
        "RELAX ONE blocker only if it is better or clearly no worse in "
        "TRAIN and also stable in 2025, both symbols, and year-by-year."
    )
    print("If evidence is mixed: KEEP ALL FILTERS.")
    print("No automatic production decision is made by this script.")
    print("\nBLOCKED_OPPORTUNITY_AUDIT_OK")


if __name__ == "__main__":
    main()
