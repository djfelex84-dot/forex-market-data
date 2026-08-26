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

NEW_YORK_TIMEZONE = ZoneInfo(
    "America/New_York"
)


def interval_to_timedelta(interval):
    if interval.endswith("min"):
        return timedelta(
            minutes=int(
                interval.replace(
                    "min",
                    "",
                )
            )
        )

    if interval.endswith("h"):
        return timedelta(
            hours=int(
                interval.replace(
                    "h",
                    "",
                )
            )
        )

    raise ValueError(
        f"Unsupported interval: {interval}"
    )


INTERVAL_DELTA = (
    interval_to_timedelta(
        INTERVAL
    )
)


def parse_utc_datetime(value):
    return datetime.strptime(
        value,
        TIME_FORMAT,
    ).replace(
        tzinfo=timezone.utc
    )


def parse_time(value):
    return datetime.strptime(
        value,
        TIME_FORMAT,
    )


def is_forex_session_open(
    candle_datetime,
):
    utc_time = (
        parse_utc_datetime(
            candle_datetime
        )
    )

    new_york_time = (
        utc_time.astimezone(
            NEW_YORK_TIMEZONE
        )
    )

    weekday = (
        new_york_time.weekday()
    )

    hour_minute = (
        new_york_time.hour,
        new_york_time.minute,
    )

    if weekday == 5:
        return False

    if weekday == 6:
        return (
            hour_minute
            >= (17, 0)
        )

    if weekday == 4:
        return (
            hour_minute
            < (17, 0)
        )

    return True


def is_market_session_open(
    symbol,
    candle_datetime,
):
    instrument = (
        get_instrument_config(
            symbol
        )
    )

    if (
        instrument.get("market")
        == "FOREX"
    ):
        return (
            is_forex_session_open(
                candle_datetime
            )
        )

    return True


def request_history_chunk(
    symbol,
    end_date=None,
):
    params = {
        "symbol": symbol,
        "interval": INTERVAL,
        "outputsize":
            API_CHUNK_SIZE,
        "timezone": "UTC",
        "apikey":
            TWELVE_DATA_API_KEY,
    }

    if end_date is not None:
        params["end_date"] = (
            end_date
        )

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

    values = (
        data.get("values")
        or []
    )

    if not values:
        raise RuntimeError(
            "No historical candles "
            f"received for {symbol}"
        )

    return values


def fetch_historical_candles(
    symbol,
):
    if not TWELVE_DATA_API_KEY:
        raise RuntimeError(
            "TWELVE_DATA_API_KEY "
            "is not set"
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
        values = (
            request_history_chunk(
                symbol,
                end_date=end_date,
            )
        )

        newest = (
            values[0]["datetime"]
        )

        oldest = (
            values[-1]["datetime"]
        )

        print(
            f"CHUNK "
            f"{chunk_number}/"
            f"{chunks_needed} | "
            f"{symbol} | "
            f"Candles="
            f"{len(values)} | "
            f"Newest={newest} | "
            f"Oldest={oldest}"
        )

        for candle in values:
            by_datetime[
                candle["datetime"]
            ] = candle

        oldest_time = (
            parse_utc_datetime(
                oldest
            )
        )

        next_end = (
            oldest_time
            - INTERVAL_DELTA
        )

        end_date = (
            next_end.strftime(
                TIME_FORMAT
            )
        )

        if (
            len(values)
            < API_CHUNK_SIZE
        ):
            break

        if (
            chunk_number
            < chunks_needed
        ):
            print(
                "WAIT | "
                f"{REQUEST_DELAY_SECONDS}s "
                "to respect Twelve Data "
                "rate limits"
            )

            time.sleep(
                REQUEST_DELAY_SECONDS
            )

    ordered_values = sorted(
        by_datetime.values(),
        key=lambda candle:
            parse_utc_datetime(
                candle["datetime"]
            ),
    )

    if (
        len(ordered_values)
        > TOTAL_HISTORY_CANDLES
    ):
        ordered_values = (
            ordered_values[
                -TOTAL_HISTORY_CANDLES:
            ]
        )

    now = datetime.now(
        timezone.utc
    )

    raw_closed_count = 0
    session_excluded_count = 0

    candles = []

    for candle in ordered_values:
        candle_open = (
            parse_utc_datetime(
                candle["datetime"]
            )
        )

        candle_close = (
            candle_open
            + INTERVAL_DELTA
        )

        if candle_close > now:
            continue

        raw_closed_count += 1

        if not (
            is_market_session_open(
                symbol,
                candle["datetime"],
            )
        ):
            session_excluded_count += 1
            continue

        candles.append(
            {
                "datetime":
                    candle["datetime"],
                "open":
                    float(
                        candle["open"]
                    ),
                "high":
                    float(
                        candle["high"]
                    ),
                "low":
                    float(
                        candle["low"]
                    ),
                "close":
                    float(
                        candle["close"]
                    ),
            }
        )

    if len(candles) < CANDLE_LIMIT:
        raise RuntimeError(
            "Not enough session candles "
            f"for {symbol}: "
            f"{len(candles)}"
        )

    return (
        candles,
        raw_closed_count,
        session_excluded_count,
    )


def is_new_signal(
    previous_result,
    current_result,
):
    if (
        current_result["status"]
        != "VALID"
    ):
        return False

    if current_result[
        "signal"
    ] not in (
        "BUY",
        "SELL",
    ):
        return False

    if previous_result is None:
        return True

    if (
        previous_result["status"]
        != "VALID"
        or previous_result[
            "signal"
        ]
        != current_result[
            "signal"
        ]
    ):
        return True

    current_time = (
        parse_utc_datetime(
            current_result[
                "datetime"
            ]
        )
    )

    previous_time = (
        parse_utc_datetime(
            previous_result[
                "datetime"
            ]
        )
    )

    return (
        current_time
        - previous_time
    ) != INTERVAL_DELTA


def build_trade(
    symbol,
    result,
    candles,
    entry_index,
):
    instrument = (
        get_instrument_config(
            symbol
        )
    )

    pip_size = (
        instrument[
            "pip_size"
        ]
    )

    min_stop_pips = (
        instrument[
            "min_stop_pips"
        ]
    )

    spread_pips = (
        instrument[
            "assumed_spread_pips"
        ]
    )

    signal = result[
        "signal"
    ]

    entry_price = float(
        result["close"]
    )

    atr = float(
        result["atr"]
    )

    stop_distance = max(
        atr
        * STOP_LOSS_ATR_MULTIPLIER,
        min_stop_pips
        * pip_size,
    )

    risk_pips = (
        stop_distance
        / pip_size
    )

    reward_distance = (
        stop_distance
        * TAKE_PROFIT_R_MULTIPLE
    )

    reward_pips = (
        reward_distance
        / pip_size
    )

    if signal == "BUY":
        stop_loss = (
            entry_price
            - stop_distance
        )

        take_profit = (
            entry_price
            + reward_distance
        )

    else:
        stop_loss = (
            entry_price
            + stop_distance
        )

        take_profit = (
            entry_price
            - reward_distance
        )

    entry_time = (
        parse_utc_datetime(
            result["datetime"]
        )
    )

    exit_reason = None
    exit_price = None
    exit_index = None

    for future_index in range(
        entry_index + 1,
        len(candles),
    ):
        candle = (
            candles[
                future_index
            ]
        )

        future_time = (
            parse_utc_datetime(
                candle["datetime"]
            )
        )

        elapsed_minutes = (
            (
                future_time
                - entry_time
            ).total_seconds()
            / 60
        )

        if signal == "BUY":
            sl_hit = (
                candle["low"]
                <= stop_loss
            )

            tp_hit = (
                candle["high"]
                >= take_profit
            )

        else:
            sl_hit = (
                candle["high"]
                >= stop_loss
            )

            tp_hit = (
                candle["low"]
                <= take_profit
            )

        if sl_hit and tp_hit:
            exit_reason = (
                "AMBIGUOUS"
            )
            exit_index = (
                future_index
            )
            break

        if sl_hit:
            exit_reason = (
                "STOP_LOSS"
            )
            exit_price = (
                stop_loss
            )
            exit_index = (
                future_index
            )
            break

        if tp_hit:
            exit_reason = (
                "TAKE_PROFIT"
            )
            exit_price = (
                take_profit
            )
            exit_index = (
                future_index
            )
            break

        if (
            elapsed_minutes
            >= MAX_TRADE_MINUTES
        ):
            exit_reason = (
                "TIMEOUT"
            )

            exit_price = (
                candle["close"]
            )

            exit_index = (
                future_index
            )

            break

    if exit_reason is None:
        return None

    if (
        exit_reason
        == "AMBIGUOUS"
    ):
        net_pnl_pips = None
        r_multiple = None

    else:
        if signal == "BUY":
            gross_pnl_pips = (
                exit_price
                - entry_price
            ) / pip_size

        else:
            gross_pnl_pips = (
                entry_price
                - exit_price
            ) / pip_size

        net_pnl_pips = (
            gross_pnl_pips
            - spread_pips
        )

        r_multiple = (
            net_pnl_pips
            / risk_pips
        )

    return {
        "symbol":
            symbol,
        "signal":
            signal,
        "entry_time":
            result["datetime"],
        "exit_time":
            candles[
                exit_index
            ]["datetime"],
        "entry_price":
            entry_price,
        "stop_loss":
            stop_loss,
        "take_profit":
            take_profit,
        "risk_pips":
            risk_pips,
        "reward_pips":
            reward_pips,
        "exit_reason":
            exit_reason,
        "net_pnl_pips":
            net_pnl_pips,
        "r_multiple":
            r_multiple,
        "setup_score":
            result.get(
                "setup_score"
            ),
        "rsi":
            result.get(
                "rsi"
            ),
        "atr":
            result.get(
                "atr"
            ),
        "ema_distance_atr":
            result.get(
                "ema_distance_atr"
            ),
        "ema_direction":
            result.get(
                "ema_direction"
            ),
        "trend":
            result.get(
                "trend"
            ),
    }


def backtest_symbol(
    symbol,
    candles,
):
    trades = []

    previous_result = None

    for index in range(
        CANDLE_LIMIT - 1,
        len(candles),
    ):
        window = candles[
            index
            - CANDLE_LIMIT
            + 1:
            index + 1
        ]

        result = (
            analyze_market(
                window,
                symbol,
            )
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
                trades.append(
                    trade
                )

        previous_result = (
            result
        )

    return trades


def apply_one_open_per_symbol(
    trades,
):
    selected = []
    open_until = {}

    for trade in sorted(
        trades,
        key=lambda item:
            parse_time(
                item[
                    "entry_time"
                ]
            ),
    ):
        symbol = trade[
            "symbol"
        ]

        entry_time = (
            parse_time(
                trade[
                    "entry_time"
                ]
            )
        )

        blocked_until = (
            open_until.get(
                symbol
            )
        )

        if (
            blocked_until
            is not None
            and entry_time
            <= blocked_until
        ):
            continue

        selected.append(
            trade
        )

        open_until[
            symbol
        ] = parse_time(
            trade[
                "exit_time"
            ]
        )

    return selected


def profit_factor(
    trades,
):
    positive = sum(
        trade[
            "net_pnl_pips"
        ]
        for trade in trades
        if (
            trade[
                "net_pnl_pips"
            ] is not None
            and trade[
                "net_pnl_pips"
            ] > 0
        )
    )

    negative = abs(
        sum(
            trade[
                "net_pnl_pips"
            ]
            for trade in trades
            if (
                trade[
                    "net_pnl_pips"
                ] is not None
                and trade[
                    "net_pnl_pips"
                ] < 0
            )
        )
    )

    if negative == 0:
        if positive > 0:
            return math.inf

        return 0.0

    return (
        positive
        / negative
    )


def max_losing_streak(
    trades,
):
    maximum = 0
    current = 0

    for trade in sorted(
        trades,
        key=lambda item:
            parse_time(
                item[
                    "entry_time"
                ]
            ),
    ):
        pnl = trade[
            "net_pnl_pips"
        ]

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


def print_stats(
    title,
    trades,
):
    resolved = [
        trade
        for trade in trades
        if trade[
            "net_pnl_pips"
        ] is not None
    ]

    tp_count = sum(
        1
        for trade in trades
        if trade[
            "exit_reason"
        ] == "TAKE_PROFIT"
    )

    sl_count = sum(
        1
        for trade in trades
        if trade[
            "exit_reason"
        ] == "STOP_LOSS"
    )

    timeout_count = sum(
        1
        for trade in trades
        if trade[
            "exit_reason"
        ] == "TIMEOUT"
    )

    ambiguous_count = sum(
        1
        for trade in trades
        if trade[
            "exit_reason"
        ] == "AMBIGUOUS"
    )

    positive_count = sum(
        1
        for trade in resolved
        if trade[
            "net_pnl_pips"
        ] > 0
    )

    net_pips = sum(
        trade[
            "net_pnl_pips"
        ]
        for trade in resolved
    )

    r_values = [
        trade[
            "r_multiple"
        ]
        for trade in resolved
        if trade[
            "r_multiple"
        ] is not None
    ]

    average_r = (
        sum(r_values)
        / len(r_values)
        if r_values
        else 0.0
    )

    positive_rate = (
        positive_count
        / len(resolved)
        * 100
        if resolved
        else 0.0
    )

    pf = (
        profit_factor(
            trades
        )
    )

    if math.isinf(pf):
        pf_text = "INF"
    else:
        pf_text = (
            f"{pf:.2f}"
        )

    print()

    print(
        "================================"
    )

    print(
        title
    )

    print(
        "================================"
    )

    print(
        f"Trades: "
        f"{len(trades)}"
    )

    print(
        f"TP: {tp_count} | "
        f"SL: {sl_count} | "
        f"TIMEOUT: "
        f"{timeout_count} | "
        f"AMBIGUOUS: "
        f"{ambiguous_count}"
    )

    print(
        "Positive net trades: "
        f"{positive_count}/"
        f"{len(resolved)} "
        f"({positive_rate:.1f}%)"
    )

    print(
        f"Net pips: "
        f"{net_pips:+.2f}"
    )

    print(
        f"Average R: "
        f"{average_r:+.3f}"
    )

    print(
        f"Profit factor: "
        f"{pf_text}"
    )

    print(
        "Max losing streak: "
        f"{max_losing_streak(trades)}"
    )


def split_train_test(
    trades,
    train_ratio=0.70,
):
    ordered = sorted(
        trades,
        key=lambda item:
            parse_time(
                item[
                    "entry_time"
                ]
            ),
    )

    if not ordered:
        return [], []

    split_index = int(
        len(ordered)
        * train_ratio
    )

    split_index = max(
        1,
        min(
            split_index,
            len(ordered),
        ),
    )

    return (
        ordered[
            :split_index
        ],
        ordered[
            split_index:
        ],
    )


def main():
    all_trades = []

    print(
        "AS HISTORICAL "
        "BACKTEST V2"
    )

    print(
        f"Interval: "
        f"{INTERVAL}"
    )

    print(
        "Historical candles "
        f"target: "
        f"{TOTAL_HISTORY_CANDLES}"
    )

    print(
        f"API chunk size: "
        f"{API_CHUNK_SIZE}"
    )

    print(
        "Forex session: "
        "Sun 17:00 -> "
        "Fri 17:00 "
        "America/New_York"
    )

    print(
        "One-open-trade "
        "comparison: ENABLED"
    )

    print(
        "Live bot / Telegram / "
        "SQLite are NOT modified."
    )

    for symbol in SYMBOLS:
        print()

        print(
            f"FETCH | {symbol}"
        )

        (
            candles,
            raw_closed_count,
            session_excluded_count,
        ) = (
            fetch_historical_candles(
                symbol
            )
        )

        print(
            f"DATA | {symbol} | "
            f"RawClosed="
            f"{raw_closed_count} | "
            f"SessionCandles="
            f"{len(candles)} | "
            f"Excluded="
            f"{session_excluded_count} | "
            f"From="
            f"{candles[0]['datetime']} | "
            f"To="
            f"{candles[-1]['datetime']}"
        )

        trades = (
            backtest_symbol(
                symbol,
                candles,
            )
        )

        all_trades.extend(
            trades
        )

        print_stats(
            f"{symbol} | "
            "CURRENT STRATEGY",
            trades,
        )

        print_stats(
            f"{symbol} | "
            "ONE OPEN TRADE",
            apply_one_open_per_symbol(
                trades
            ),
        )

    all_trades = sorted(
        all_trades,
        key=lambda item:
            parse_time(
                item[
                    "entry_time"
                ]
            ),
    )

    print_stats(
        "ALL SYMBOLS | "
        "CURRENT STRATEGY",
        all_trades,
    )

    one_open_all = (
        apply_one_open_per_symbol(
            all_trades
        )
    )

    print_stats(
        "ALL SYMBOLS | "
        "ONE OPEN TRADE",
        one_open_all,
    )

    train, test = (
        split_train_test(
            one_open_all
        )
    )

    print_stats(
        "ONE OPEN | "
        "FIRST 70% "
        "(RESEARCH)",
        train,
    )

    print_stats(
        "ONE OPEN | "
        "LAST 30% "
        "(HOLDOUT)",
        test,
    )

    print()

    print(
        "BACKTEST COMPLETE"
    )


if __name__ == "__main__":
    main()
