import math
import time
import requests

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import (
    TWELVE_DATA_API_KEY,
    SYMBOLS,
    INTERVAL,
    CANDLE_LIMIT,
    STOP_LOSS_ATR_MULTIPLIER,
    TAKE_PROFIT_R_MULTIPLE,
    MAX_TRADE_MINUTES,
    get_instrument_config,
)

from strategy import analyze_market


API_URL = "https://api.twelvedata.com/time_series"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

TOTAL_HISTORY_CANDLES = 20000
API_CHUNK_SIZE = 5000
REQUEST_DELAY_SECONDS = 10
TRAIN_RATIO = 0.70
MIN_SAMPLE_NOTE = 30

NEW_YORK_TIMEZONE = ZoneInfo("America/New_York")


def interval_to_timedelta(interval):
    if interval.endswith("min"):
        return timedelta(
            minutes=int(interval.replace("min", ""))
        )

    if interval.endswith("h"):
        return timedelta(
            hours=int(interval.replace("h", ""))
        )

    raise ValueError(f"Unsupported interval: {interval}")


INTERVAL_DELTA = interval_to_timedelta(INTERVAL)


def parse_utc_datetime(value):
    return datetime.strptime(
        value,
        TIME_FORMAT,
    ).replace(tzinfo=timezone.utc)


def parse_time(value):
    return datetime.strptime(
        value,
        TIME_FORMAT,
    )


def is_forex_session_open(candle_datetime):
    utc_time = parse_utc_datetime(candle_datetime)
    new_york_time = utc_time.astimezone(NEW_YORK_TIMEZONE)

    weekday = new_york_time.weekday()
    hour_minute = (
        new_york_time.hour,
        new_york_time.minute,
    )

    if weekday == 5:
        return False

    if weekday == 6:
        return hour_minute >= (17, 0)

    if weekday == 4:
        return hour_minute < (17, 0)

    return True


def is_market_session_open(symbol, candle_datetime):
    instrument = get_instrument_config(symbol)

    if instrument.get("market") == "FOREX":
        return is_forex_session_open(candle_datetime)

    return True


def request_history_chunk(symbol, end_date=None):
    params = {
        "symbol": symbol,
        "interval": INTERVAL,
        "outputsize": API_CHUNK_SIZE,
        "timezone": "UTC",
        "apikey": TWELVE_DATA_API_KEY,
    }

    if end_date is not None:
        params["end_date"] = end_date

    response = requests.get(
        API_URL,
        params=params,
        timeout=30,
    )

    response.raise_for_status()
    data = response.json()

    if data.get("status") == "error":
        raise RuntimeError(
            data.get(
                "message",
                "Twelve Data API error",
            )
        )

    values = data.get("values") or []

    if not values:
        raise RuntimeError(
            "No historical candles "
            f"received for {symbol}"
        )

    return values


def fetch_historical_candles(symbol):
    if not TWELVE_DATA_API_KEY:
        raise RuntimeError(
            "TWELVE_DATA_API_KEY is not set"
        )

    chunks_needed = math.ceil(
        TOTAL_HISTORY_CANDLES
        / API_CHUNK_SIZE
    )

    by_datetime = {}
    end_date = None

    for chunk_number in range(
        1,
        chunks_needed + 1,
    ):
        values = request_history_chunk(
            symbol,
            end_date=end_date,
        )

        newest = values[0]["datetime"]
        oldest = values[-1]["datetime"]

        print(
            f"CHUNK {chunk_number}/"
            f"{chunks_needed} | "
            f"{symbol} | "
            f"Candles={len(values)} | "
            f"Newest={newest} | "
            f"Oldest={oldest}"
        )

        for candle in values:
            by_datetime[
                candle["datetime"]
            ] = candle

        oldest_time = parse_utc_datetime(oldest)
        next_end = oldest_time - INTERVAL_DELTA
        end_date = next_end.strftime(TIME_FORMAT)

        if len(values) < API_CHUNK_SIZE:
            break

        if chunk_number < chunks_needed:
            print(
                "WAIT | "
                f"{REQUEST_DELAY_SECONDS}s"
            )
            time.sleep(REQUEST_DELAY_SECONDS)

    ordered_values = sorted(
        by_datetime.values(),
        key=lambda candle: parse_utc_datetime(
            candle["datetime"]
        ),
    )

    if len(ordered_values) > TOTAL_HISTORY_CANDLES:
        ordered_values = ordered_values[
            -TOTAL_HISTORY_CANDLES:
        ]

    now = datetime.now(timezone.utc)

    raw_closed_count = 0
    session_excluded_count = 0
    candles = []

    for candle in ordered_values:
        candle_open = parse_utc_datetime(
            candle["datetime"]
        )
        candle_close = candle_open + INTERVAL_DELTA

        if candle_close > now:
            continue

        raw_closed_count += 1

        if not is_market_session_open(
            symbol,
            candle["datetime"],
        ):
            session_excluded_count += 1
            continue

        candles.append(
            {
                "datetime": candle["datetime"],
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
            }
        )

    if len(candles) < CANDLE_LIMIT:
        raise RuntimeError(
            "Not enough session candles "
            f"for {symbol}: {len(candles)}"
        )

    return (
        candles,
        raw_closed_count,
        session_excluded_count,
    )


def is_new_signal(previous_result, current_result):
    if current_result["status"] != "VALID":
        return False

    if current_result["signal"] not in (
        "BUY",
        "SELL",
    ):
        return False

    if previous_result is None:
        return True

    if (
        previous_result["status"] != "VALID"
        or previous_result["signal"]
        != current_result["signal"]
    ):
        return True

    current_time = parse_utc_datetime(
        current_result["datetime"]
    )
    previous_time = parse_utc_datetime(
        previous_result["datetime"]
    )

    return (
        current_time - previous_time
    ) != INTERVAL_DELTA


def build_trade(
    symbol,
    result,
    candles,
    entry_index,
):
    instrument = get_instrument_config(symbol)

    pip_size = instrument["pip_size"]
    min_stop_pips = instrument["min_stop_pips"]
    spread_pips = instrument["assumed_spread_pips"]

    signal = result["signal"]
    entry_price = float(result["close"])
    atr = float(result["atr"])

    stop_distance = max(
        atr * STOP_LOSS_ATR_MULTIPLIER,
        min_stop_pips * pip_size,
    )

    risk_pips = stop_distance / pip_size

    reward_distance = (
        stop_distance
        * TAKE_PROFIT_R_MULTIPLE
    )

    if signal == "BUY":
        stop_loss = entry_price - stop_distance
        take_profit = entry_price + reward_distance
    else:
        stop_loss = entry_price + stop_distance
        take_profit = entry_price - reward_distance

    entry_time = parse_utc_datetime(
        result["datetime"]
    )

    exit_reason = None
    exit_price = None
    exit_index = None

    for future_index in range(
        entry_index + 1,
        len(candles),
    ):
        candle = candles[future_index]

        future_time = parse_utc_datetime(
            candle["datetime"]
        )

        elapsed_minutes = (
            future_time - entry_time
        ).total_seconds() / 60

        if signal == "BUY":
            sl_hit = candle["low"] <= stop_loss
            tp_hit = candle["high"] >= take_profit
        else:
            sl_hit = candle["high"] >= stop_loss
            tp_hit = candle["low"] <= take_profit

        if sl_hit and tp_hit:
            exit_reason = "AMBIGUOUS"
            exit_index = future_index
            break

        if sl_hit:
            exit_reason = "STOP_LOSS"
            exit_price = stop_loss
            exit_index = future_index
            break

        if tp_hit:
            exit_reason = "TAKE_PROFIT"
            exit_price = take_profit
            exit_index = future_index
            break

        if elapsed_minutes >= MAX_TRADE_MINUTES:
            exit_reason = "TIMEOUT"
            exit_price = candle["close"]
            exit_index = future_index
            break

    if exit_reason is None:
        return None

    if exit_reason == "AMBIGUOUS":
        net_pnl_pips = None
        r_multiple = None

    else:
        if signal == "BUY":
            gross_pnl_pips = (
                exit_price - entry_price
            ) / pip_size
        else:
            gross_pnl_pips = (
                entry_price - exit_price
            ) / pip_size

        net_pnl_pips = (
            gross_pnl_pips - spread_pips
        )

        r_multiple = (
            net_pnl_pips / risk_pips
        )

    return {
        "symbol": symbol,
        "signal": signal,
        "entry_time": result["datetime"],
        "exit_time": candles[
            exit_index
        ]["datetime"],
        "exit_reason": exit_reason,
        "net_pnl_pips": net_pnl_pips,
        "r_multiple": r_multiple,
        "rsi": result.get("rsi"),
        "atr": result.get("atr"),
        "ema_distance_atr": result.get(
            "ema_distance_atr"
        ),
        "setup_score": result.get(
            "setup_score"
        ),
    }


def backtest_symbol(symbol, candles):
    trades = []
    previous_result = None

    for index in range(
        CANDLE_LIMIT - 1,
        len(candles),
    ):
        window = candles[
            index - CANDLE_LIMIT + 1:
            index + 1
        ]

        result = analyze_market(
            window,
            symbol,
        )

        if is_new_signal(
            previous_result,
            result,
        ):
            trade = build_trade(
                symbol,
                result,
                candles,
                index,
            )

            if trade is not None:
                trades.append(trade)

        previous_result = result

    return trades


def apply_one_open_per_symbol(trades):
    selected = []
    open_until = {}

    for trade in sorted(
        trades,
        key=lambda item: parse_time(
            item["entry_time"]
        ),
    ):
        symbol = trade["symbol"]

        entry_time = parse_time(
            trade["entry_time"]
        )

        blocked_until = open_until.get(symbol)

        if (
            blocked_until is not None
            and entry_time <= blocked_until
        ):
            continue

        selected.append(trade)

        open_until[symbol] = parse_time(
            trade["exit_time"]
        )

    return selected


def split_train_holdout(trades):
    ordered = sorted(
        trades,
        key=lambda item: parse_time(
            item["entry_time"]
        ),
    )

    if not ordered:
        return [], []

    split_index = int(
        len(ordered) * TRAIN_RATIO
    )

    split_index = max(
        1,
        min(split_index, len(ordered)),
    )

    return (
        ordered[:split_index],
        ordered[split_index:],
    )


def profit_factor(trades):
    positive = sum(
        trade["net_pnl_pips"]
        for trade in trades
        if (
            trade["net_pnl_pips"] is not None
            and trade["net_pnl_pips"] > 0
        )
    )

    negative = abs(
        sum(
            trade["net_pnl_pips"]
            for trade in trades
            if (
                trade["net_pnl_pips"] is not None
                and trade["net_pnl_pips"] < 0
            )
        )
    )

    if negative == 0:
        if positive > 0:
            return math.inf

        return 0.0

    return positive / negative


def max_losing_streak(trades):
    maximum = 0
    current = 0

    for trade in sorted(
        trades,
        key=lambda item: parse_time(
            item["entry_time"]
        ),
    ):
        pnl = trade["net_pnl_pips"]

        if pnl is None:
            continue

        if pnl < 0:
            current += 1
            maximum = max(
                maximum,
                current,
            )
        else:
            current = 0

    return maximum


def calculate_stats(trades):
    resolved = [
        trade
        for trade in trades
        if trade["net_pnl_pips"] is not None
    ]

    tp = sum(
        1
        for trade in trades
        if trade["exit_reason"] == "TAKE_PROFIT"
    )

    sl = sum(
        1
        for trade in trades
        if trade["exit_reason"] == "STOP_LOSS"
    )

    timeout = sum(
        1
        for trade in trades
        if trade["exit_reason"] == "TIMEOUT"
    )

    ambiguous = sum(
        1
        for trade in trades
        if trade["exit_reason"] == "AMBIGUOUS"
    )

    positive = sum(
        1
        for trade in resolved
        if trade["net_pnl_pips"] > 0
    )

    net_pips = sum(
        trade["net_pnl_pips"]
        for trade in resolved
    )

    r_values = [
        trade["r_multiple"]
        for trade in resolved
        if trade["r_multiple"] is not None
    ]

    average_r = (
        sum(r_values) / len(r_values)
        if r_values
        else 0.0
    )

    positive_rate = (
        positive / len(resolved) * 100
        if resolved
        else 0.0
    )

    return {
        "trades": len(trades),
        "resolved": len(resolved),
        "tp": tp,
        "sl": sl,
        "timeout": timeout,
        "ambiguous": ambiguous,
        "positive_rate": positive_rate,
        "net_pips": net_pips,
        "average_r": average_r,
        "profit_factor": profit_factor(trades),
        "max_losing_streak": max_losing_streak(trades),
    }


def format_pf(value):
    if math.isinf(value):
        return "INF"

    return f"{value:.2f}"


def print_summary(title, trades):
    stats = calculate_stats(trades)

    print()
    print("========================================")
    print(title)
    print("========================================")

    print(
        f"Trades={stats['trades']} | "
        f"TP={stats['tp']} | "
        f"SL={stats['sl']} | "
        f"TIMEOUT={stats['timeout']} | "
        f"AMB={stats['ambiguous']}"
    )

    print(
        f"Positive={stats['positive_rate']:.1f}% | "
        f"NetPips={stats['net_pips']:+.2f} | "
        f"AvgR={stats['average_r']:+.3f} | "
        f"PF={format_pf(stats['profit_factor'])} | "
        f"MaxLS={stats['max_losing_streak']}"
    )


def print_group_table(title, groups):
    print()
    print("========================================")
    print(title)
    print("========================================")

    print(
        f"{'GROUP':<26} "
        f"{'N':>5} "
        f"{'TP':>5} "
        f"{'SL':>5} "
        f"{'TO':>5} "
        f"{'POS%':>7} "
        f"{'NET':>10} "
        f"{'AVGR':>8} "
        f"{'PF':>7} "
        f"NOTE"
    )

    for label, trades in groups:
        stats = calculate_stats(trades)

        note = (
            "SMALL"
            if stats["trades"] < MIN_SAMPLE_NOTE
            else ""
        )

        print(
            f"{label:<26} "
            f"{stats['trades']:>5} "
            f"{stats['tp']:>5} "
            f"{stats['sl']:>5} "
            f"{stats['timeout']:>5} "
            f"{stats['positive_rate']:>6.1f}% "
            f"{stats['net_pips']:>+10.1f} "
            f"{stats['average_r']:>+8.3f} "
            f"{format_pf(stats['profit_factor']):>7} "
            f"{note}"
        )


def group_by_values(trades, key, labels):
    groups = []

    for value, label in labels:
        group = [
            trade
            for trade in trades
            if trade.get(key) == value
        ]

        groups.append(
            (label, group)
        )

    return groups


def ema_distance_bucket(value):
    if value is None:
        return "UNKNOWN"

    value = float(value)

    if value < 0.25:
        return "0.15-0.25"

    if value < 0.40:
        return "0.25-0.40"

    if value < 0.60:
        return "0.40-0.60"

    if value < 0.80:
        return "0.60-0.80"

    if value < 1.00:
        return "0.80-1.00"

    if value < 1.50:
        return "1.00-1.50"

    return "1.50+"


def rsi_bucket(trade):
    value = trade.get("rsi")
    signal = trade.get("signal")

    if value is None:
        return "UNKNOWN"

    value = float(value)

    if signal == "BUY":
        if value < 56:
            return "BUY 52-56"

        if value < 60:
            return "BUY 56-60"

        if value < 64:
            return "BUY 60-64"

        return "BUY 64-68"

    if signal == "SELL":
        if value < 36:
            return "SELL 32-36"

        if value < 40:
            return "SELL 36-40"

        if value < 44:
            return "SELL 40-44"

        return "SELL 44-48"

    return "UNKNOWN"


def utc_session_bucket(entry_time):
    hour = parse_utc_datetime(
        entry_time
    ).hour

    if hour < 7:
        return "00-07 Asia"

    if hour < 12:
        return "07-12 London"

    if hour < 16:
        return "12-16 Overlap"

    if hour < 21:
        return "16-21 New York"

    return "21-24 Late"


def weekday_bucket(entry_time):
    names = (
        "Mon",
        "Tue",
        "Wed",
        "Thu",
        "Fri",
        "Sat",
        "Sun",
    )

    weekday = parse_utc_datetime(
        entry_time
    ).weekday()

    return names[weekday]


def build_bucket_groups(
    trades,
    bucket_function,
    order,
):
    buckets = {
        label: []
        for label in order
    }

    for trade in trades:
        label = bucket_function(trade)

        if label not in buckets:
            buckets[label] = []

        buckets[label].append(trade)

    groups = [
        (label, buckets[label])
        for label in order
    ]

    extra_labels = [
        label
        for label in buckets
        if label not in order
    ]

    for label in extra_labels:
        groups.append(
            (label, buckets[label])
        )

    return groups


def main():
    print("AS STRATEGY RESEARCH V1")
    print(f"Interval: {INTERVAL}")

    print(
        "Historical candles target: "
        f"{TOTAL_HISTORY_CANDLES} per symbol"
    )

    print(
        "Forex session filter: ENABLED"
    )

    print(
        "One open trade per symbol: ENABLED"
    )

    print(
        "Research split: first 70% only"
    )

    print(
        "Holdout: last 30% LOCKED "
        "and not bucket-analyzed"
    )

    print(
        "Live bot / Telegram / SQLite "
        "are NOT modified."
    )

    all_trades = []

    for symbol in SYMBOLS:
        print()
        print(f"FETCH | {symbol}")

        (
            candles,
            raw_closed_count,
            session_excluded_count,
        ) = fetch_historical_candles(symbol)

        print(
            f"DATA | {symbol} | "
            f"RawClosed={raw_closed_count} | "
            f"SessionCandles={len(candles)} | "
            f"Excluded={session_excluded_count} | "
            f"From={candles[0]['datetime']} | "
            f"To={candles[-1]['datetime']}"
        )

        trades = backtest_symbol(
            symbol,
            candles,
        )

        all_trades.extend(trades)

    one_open_all = apply_one_open_per_symbol(
        all_trades
    )

    (
        research_trades,
        holdout_trades,
    ) = split_train_holdout(
        one_open_all
    )

    print_summary(
        "ALL ONE-OPEN TRADES | FULL BASELINE",
        one_open_all,
    )

    print_summary(
        "RESEARCH SET | FIRST 70%",
        research_trades,
    )

    print()
    print("========================================")
    print("HOLDOUT | LAST 30%")
    print("========================================")

    print(
        f"Trades={len(holdout_trades)} | "
        "LOCKED — no bucket analysis in V1"
    )

    symbol_groups = [
        (
            symbol,
            [
                trade
                for trade in research_trades
                if trade["symbol"] == symbol
            ],
        )
        for symbol in SYMBOLS
    ]

    print_group_table(
        "RESEARCH | BY SYMBOL",
        symbol_groups,
    )

    print_group_table(
        "RESEARCH | BY DIRECTION",
        group_by_values(
            research_trades,
            "signal",
            [
                ("BUY", "BUY"),
                ("SELL", "SELL"),
            ],
        ),
    )

    symbol_direction_groups = []

    for symbol in SYMBOLS:
        for signal in (
            "BUY",
            "SELL",
        ):
            symbol_direction_groups.append(
                (
                    f"{symbol} {signal}",
                    [
                        trade
                        for trade in research_trades
                        if (
                            trade["symbol"] == symbol
                            and trade["signal"] == signal
                        )
                    ],
                )
            )

    print_group_table(
        "RESEARCH | SYMBOL x DIRECTION",
        symbol_direction_groups,
    )

    ema_order = [
        "0.15-0.25",
        "0.25-0.40",
        "0.40-0.60",
        "0.60-0.80",
        "0.80-1.00",
        "1.00-1.50",
        "1.50+",
        "UNKNOWN",
    ]

    print_group_table(
        "RESEARCH | EMA DISTANCE / ATR",
        build_bucket_groups(
            research_trades,
            lambda trade: ema_distance_bucket(
                trade.get(
                    "ema_distance_atr"
                )
            ),
            ema_order,
        ),
    )

    rsi_order = [
        "BUY 52-56",
        "BUY 56-60",
        "BUY 60-64",
        "BUY 64-68",
        "SELL 32-36",
        "SELL 36-40",
        "SELL 40-44",
        "SELL 44-48",
        "UNKNOWN",
    ]

    print_group_table(
        "RESEARCH | RSI ZONES",
        build_bucket_groups(
            research_trades,
            rsi_bucket,
            rsi_order,
        ),
    )

    session_order = [
        "00-07 Asia",
        "07-12 London",
        "12-16 Overlap",
        "16-21 New York",
        "21-24 Late",
    ]

    print_group_table(
        "RESEARCH | UTC TIME BUCKETS",
        build_bucket_groups(
            research_trades,
            lambda trade: utc_session_bucket(
                trade["entry_time"]
            ),
            session_order,
        ),
    )

    weekday_order = [
        "Mon",
        "Tue",
        "Wed",
        "Thu",
        "Fri",
    ]

    print_group_table(
        "RESEARCH | WEEKDAY UTC",
        build_bucket_groups(
            research_trades,
            lambda trade: weekday_bucket(
                trade["entry_time"]
            ),
            weekday_order,
        ),
    )

    print()
    print("RESEARCH COMPLETE")

    print(
        "IMPORTANT: Do not change "
        "the live strategy yet."
    )


if __name__ == "__main__":
    main()
