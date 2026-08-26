import math
import requests

from datetime import timezone


BACKTEST_URL = (
    "https://raw.githubusercontent.com/"
    "djfelex84-dot/forex-market-data/main/"
    "backtest_v2.py"
)

DEV_RATIO = 0.50
VALIDATION_RATIO = 0.20

MIN_DEV_TRADES = 50
MIN_VALIDATION_TRADES = 20

MIN_PF = 1.00


def load_backtest_v2():
    response = requests.get(
        BACKTEST_URL,
        timeout=30,
    )

    response.raise_for_status()

    namespace = {
        "__name__":
            "backtest_v2_import"
    }

    exec(
        compile(
            response.text,
            "backtest_v2.py",
            "exec",
        ),
        namespace,
    )

    return namespace


B = load_backtest_v2()


SYMBOLS = B["SYMBOLS"]
INTERVAL = B["INTERVAL"]
INTERVAL_DELTA = B[
    "INTERVAL_DELTA"
]
CANDLE_LIMIT = B[
    "CANDLE_LIMIT"
]

MAX_TRADE_MINUTES = B[
    "MAX_TRADE_MINUTES"
]

STOP_LOSS_ATR_MULTIPLIER = B[
    "STOP_LOSS_ATR_MULTIPLIER"
]

TAKE_PROFIT_R_MULTIPLE = B[
    "TAKE_PROFIT_R_MULTIPLE"
]

fetch_historical_candles = B[
    "fetch_historical_candles"
]

get_instrument_config = B[
    "get_instrument_config"
]

analyze_market = B[
    "analyze_market"
]

parse_utc_datetime = B[
    "parse_utc_datetime"
]

is_forex_session_open = B[
    "is_forex_session_open"
]

TIME_FORMAT = B[
    "TIME_FORMAT"
]


def format_time(value):
    return value.astimezone(
        timezone.utc
    ).strftime(
        TIME_FORMAT
    )


def signal_time_from_result(
    result,
):
    return (
        parse_utc_datetime(
            result["datetime"]
        )
        + INTERVAL_DELTA
    )


def signal_session_open(
    symbol,
    result,
):
    instrument = (
        get_instrument_config(
            symbol
        )
    )

    if (
        instrument.get("market")
        != "FOREX"
    ):
        return True

    signal_time = (
        signal_time_from_result(
            result
        )
    )

    return (
        is_forex_session_open(
            format_time(
                signal_time
            )
        )
    )


def is_new_signal_v2(
    symbol,
    previous_result,
    current_result,
):
    if (
        current_result["status"]
        != "VALID"
    ):
        return False

    if (
        current_result["signal"]
        not in (
            "BUY",
            "SELL",
        )
    ):
        return False

    if not signal_session_open(
        symbol,
        current_result,
    ):
        return False

    if previous_result is None:
        return True

    if (
        previous_result["status"]
        != "VALID"
        or
        previous_result["signal"]
        != current_result["signal"]
    ):
        return True

    current_open = (
        parse_utc_datetime(
            current_result[
                "datetime"
            ]
        )
    )

    previous_open = (
        parse_utc_datetime(
            previous_result[
                "datetime"
            ]
        )
    )

    return (
        current_open
        - previous_open
    ) != INTERVAL_DELTA


def build_trade_v2(
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

    signal = (
        result["signal"]
    )

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

    signal_time = (
        parse_utc_datetime(
            result["datetime"]
        )
        + INTERVAL_DELTA
    )

    previous_candle = (
        candles[
            entry_index
        ]
    )

    exit_reason = None
    exit_price = None
    exit_time = None

    for future_index in range(
        entry_index + 1,
        len(candles),
    ):
        candle = (
            candles[
                future_index
            ]
        )

        future_open = (
            parse_utc_datetime(
                candle["datetime"]
            )
        )

        future_close = (
            future_open
            + INTERVAL_DELTA
        )

        elapsed_to_open = (
            (
                future_open
                - signal_time
            ).total_seconds()
            / 60
        )

        elapsed_to_close = (
            (
                future_close
                - signal_time
            ).total_seconds()
            / 60
        )

        previous_open = (
            parse_utc_datetime(
                previous_candle[
                    "datetime"
                ]
            )
        )

        candle_gap = (
            future_open
            - previous_open
        )

        if (
            elapsed_to_open
            >= MAX_TRADE_MINUTES
            and
            candle_gap
            > INTERVAL_DELTA
        ):
            exit_reason = (
                "TIMEOUT"
            )

            exit_price = (
                previous_candle[
                    "close"
                ]
            )

            exit_time = (
                previous_open
                + INTERVAL_DELTA
            )

            break

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

        if (
            sl_hit
            and tp_hit
        ):
            exit_reason = (
                "AMBIGUOUS"
            )

            exit_time = (
                future_open
            )

            break

        if sl_hit:
            exit_reason = (
                "STOP_LOSS"
            )

            exit_price = (
                stop_loss
            )

            exit_time = (
                future_open
            )

            break

        if tp_hit:
            exit_reason = (
                "TAKE_PROFIT"
            )

            exit_price = (
                take_profit
            )

            exit_time = (
                future_open
            )

            break

        if (
            elapsed_to_close
            >= MAX_TRADE_MINUTES
        ):
            exit_reason = (
                "TIMEOUT"
            )

            exit_price = (
                candle["close"]
            )

            exit_time = (
                future_close
            )

            break

        previous_candle = (
            candle
        )

    if exit_reason is None:
        return None

    if (
        exit_reason
        == "AMBIGUOUS"
    ):
        net_pips = None
        r_multiple = None

    else:
        if signal == "BUY":
            gross_pips = (
                entry_price
                and (
                    exit_price
                    - entry_price
                )
                / pip_size
            )

        else:
            gross_pips = (
                entry_price
                - exit_price
            ) / pip_size

        net_pips = (
            gross_pips
            - spread_pips
        )

        r_multiple = (
            net_pips
            / risk_pips
        )

    return {
        "symbol":
            symbol,

        "signal":
            signal,

        "signal_time":
            format_time(
                signal_time
            ),

        "exit_time":
            format_time(
                exit_time
            ),

        "exit_reason":
            exit_reason,

        "net_pnl_pips":
            net_pips,

        "r_multiple":
            r_multiple,

        "rsi":
            result.get(
                "rsi"
            ),

        "ema_distance_atr":
            result.get(
                "ema_distance_atr"
            ),
    }


def backtest_symbol_v2(
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

        if is_new_signal_v2(
            symbol,
            previous_result,
            result,
        ):
            trade = (
                build_trade_v2(
                    symbol,
                    result,
                    candles,
                    index,
                )
            )

            if trade is not None:
                trades.append(
                    trade
                )

        previous_result = (
            result
        )

    return trades


def trade_time(trade):
    return (
        parse_utc_datetime(
            trade[
                "signal_time"
            ]
        )
    )


def apply_one_open(
    trades,
):
    selected = []
    open_until = {}

    for trade in sorted(
        trades,
        key=trade_time,
    ):
        symbol = (
            trade["symbol"]
        )

        entry = (
            trade_time(
                trade
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
            and
            entry
            <= blocked_until
        ):
            continue

        selected.append(
            trade
        )

        open_until[
            symbol
        ] = (
            parse_utc_datetime(
                trade[
                    "exit_time"
                ]
            )
        )

    return selected


def profit_factor(
    trades,
):
    gains = sum(
        trade[
            "net_pnl_pips"
        ]
        for trade in trades
        if (
            trade[
                "net_pnl_pips"
            ] is not None
            and
            trade[
                "net_pnl_pips"
            ] > 0
        )
    )

    losses = abs(
        sum(
            trade[
                "net_pnl_pips"
            ]
            for trade in trades
            if (
                trade[
                    "net_pnl_pips"
                ] is not None
                and
                trade[
                    "net_pnl_pips"
                ] < 0
            )
        )
    )

    if losses == 0:
        if gains > 0:
            return math.inf

        return 0.0

    return (
        gains
        / losses
    )


def stats(
    trades,
):
    resolved = [
        trade
        for trade in trades
        if (
            trade[
                "net_pnl_pips"
            ]
            is not None
        )
    ]

    net = sum(
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
        if (
            trade[
                "r_multiple"
            ]
            is not None
        )
    ]

    avg_r = (
        sum(r_values)
        / len(r_values)
        if r_values
        else 0.0
    )

    return {
        "n":
            len(trades),

        "pf":
            profit_factor(
                trades
            ),

        "net":
            net,

        "avg_r":
            avg_r,
    }


def pf_text(
    value,
):
    if math.isinf(
        value
    ):
        return "INF"

    return (
        f"{value:.2f}"
    )


def ema_value(
    trade,
):
    value = trade.get(
        "ema_distance_atr"
    )

    if value is None:
        return None

    return float(
        value
    )


def rsi_value(
    trade,
):
    value = trade.get(
        "rsi"
    )

    if value is None:
        return None

    return float(
        value
    )


def ema_between(
    trade,
    low,
    high,
):
    value = (
        ema_value(
            trade
        )
    )

    return (
        value is not None
        and
        low <= value < high
    )


def before_utc_hour(
    trade,
    hour,
):
    return (
        trade_time(
            trade
        ).hour
        < hour
    )


def not_sunday(
    trade,
):
    return (
        trade_time(
            trade
        ).weekday()
        != 6
    )


def rsi_core(
    trade,
):
    value = (
        rsi_value(
            trade
        )
    )

    if value is None:
        return False

    if (
        trade["signal"]
        == "BUY"
    ):
        return (
            60
            <= value
            < 64
        )

    return (
        36
        <= value
        < 40
    )


def rsi_wide(
    trade,
):
    value = (
        rsi_value(
            trade
        )
    )

    if value is None:
        return False

    if (
        trade["signal"]
        == "BUY"
    ):
        return (
            58
            <= value
            < 64
        )

    return (
        36
        <= value
        < 44
    )


def candidates():
    return [
        (
            "BASELINE",
            lambda t:
                True,
        ),

        (
            "NO_LATE_21_24",
            lambda t:
                before_utc_hour(
                    t,
                    21,
                ),
        ),

        (
            "NO_SUNDAY",
            lambda t:
                not_sunday(
                    t
                ),
        ),

        (
            "NO_LATE_NO_SUNDAY",
            lambda t:
                (
                    before_utc_hour(
                        t,
                        21,
                    )
                    and
                    not_sunday(
                        t
                    )
                ),
        ),

        (
            "ACTIVE_00_16",
            lambda t:
                before_utc_hour(
                    t,
                    16,
                ),
        ),

        (
            "SELL_ONLY",
            lambda t:
                (
                    t["signal"]
                    == "SELL"
                ),
        ),

        (
            "EMA_025_100",
            lambda t:
                ema_between(
                    t,
                    0.25,
                    1.00,
                ),
        ),

        (
            "EMA_040_100",
            lambda t:
                ema_between(
                    t,
                    0.40,
                    1.00,
                ),
        ),

        (
            "EMA_080_100",
            lambda t:
                ema_between(
                    t,
                    0.80,
                    1.00,
                ),
        ),

        (
            "RSI_CORE",
            lambda t:
                rsi_core(
                    t
                ),
        ),

        (
            "RSI_WIDE",
            lambda t:
                rsi_wide(
                    t
                ),
        ),

        (
            "NO_LATE_RSI_CORE",
            lambda t:
                (
                    before_utc_hour(
                        t,
                        21,
                    )
                    and
                    rsi_core(
                        t
                    )
                ),
        ),

        (
            "NO_LATE_EMA025_100",
            lambda t:
                (
                    before_utc_hour(
                        t,
                        21,
                    )
                    and
                    ema_between(
                        t,
                        0.25,
                        1.00,
                    )
                ),
        ),

        (
            "NO_LATE_EMA040_100",
            lambda t:
                (
                    before_utc_hour(
                        t,
                        21,
                    )
                    and
                    ema_between(
                        t,
                        0.40,
                        1.00,
                    )
                ),
        ),

        (
            "EMA025_100_RSI_CORE",
            lambda t:
                (
                    ema_between(
                        t,
                        0.25,
                        1.00,
                    )
                    and
                    rsi_core(
                        t
                    )
                ),
        ),

        (
            "SELL_RSI36_40",
            lambda t:
                (
                    t["signal"]
                    == "SELL"
                    and
                    rsi_value(
                        t
                    )
                    is not None
                    and
                    36
                    <= rsi_value(
                        t
                    )
                    < 40
                ),
        ),

        (
            "SELL_NO_LATE_EMA025_100",
            lambda t:
                (
                    t["signal"]
                    == "SELL"
                    and
                    before_utc_hour(
                        t,
                        21,
                    )
                    and
                    ema_between(
                        t,
                        0.25,
                        1.00,
                    )
                ),
        ),
    ]


def split_boundaries(
    trades,
):
    ordered = sorted(
        trades,
        key=trade_time,
    )

    start = (
        trade_time(
            ordered[0]
        )
    )

    end = (
        trade_time(
            ordered[-1]
        )
    )

    span = (
        end - start
    )

    validation_start = (
        start
        + span
        * DEV_RATIO
    )

    holdout_start = (
        start
        + span
        * (
            DEV_RATIO
            + VALIDATION_RATIO
        )
    )

    return (
        validation_start,
        holdout_start,
    )


def split_raw(
    trades,
    validation_start,
    holdout_start,
):
    dev = []
    validation = []
    holdout = []

    for trade in trades:
        when = (
            trade_time(
                trade
            )
        )

        if (
            when
            < validation_start
        ):
            dev.append(
                trade
            )

        elif (
            when
            < holdout_start
        ):
            validation.append(
                trade
            )

        else:
            holdout.append(
                trade
            )

    return (
        dev,
        validation,
        holdout,
    )


def filtered_one_open(
    trades,
    predicate,
):
    eligible = [
        trade
        for trade in trades
        if predicate(
            trade
        )
    ]

    return (
        apply_one_open(
            eligible
        )
    )


def passes(
    dev_stats,
    val_stats,
):
    return (
        dev_stats["n"]
        >= MIN_DEV_TRADES

        and
        val_stats["n"]
        >= MIN_VALIDATION_TRADES

        and
        dev_stats["pf"]
        > MIN_PF

        and
        val_stats["pf"]
        > MIN_PF

        and
        dev_stats["net"]
        > 0

        and
        val_stats["net"]
        > 0

        and
        dev_stats["avg_r"]
        > 0

        and
        val_stats["avg_r"]
        > 0
    )


def main():
    print(
        "AS STRATEGY RESEARCH V2"
    )

    print(
        f"Interval: "
        f"{INTERVAL}"
    )

    print(
        "History: 20000 candles "
        "per symbol"
    )

    print(
        "Split: "
        "50% DEV / "
        "20% VALIDATION / "
        "30% HOLDOUT"
    )

    print(
        "Signal-time Forex session "
        "check: ENABLED"
    )

    print(
        "Candidate-specific "
        "one-open rule: ENABLED"
    )

    print(
        "HOLDOUT: LOCKED"
    )

    print(
        "Live bot / Telegram / "
        "SQLite are NOT modified."
    )

    all_raw = []

    for symbol in SYMBOLS:
        print()

        print(
            f"FETCH | "
            f"{symbol}"
        )

        (
            candles,
            raw_closed,
            excluded,
        ) = (
            fetch_historical_candles(
                symbol
            )
        )

        print(
            f"DATA | "
            f"{symbol} | "
            f"RawClosed="
            f"{raw_closed} | "
            f"SessionCandles="
            f"{len(candles)} | "
            f"Excluded="
            f"{excluded} | "
            f"From="
            f"{candles[0]['datetime']} | "
            f"To="
            f"{candles[-1]['datetime']}"
        )

        raw_trades = (
            backtest_symbol_v2(
                symbol,
                candles,
            )
        )

        print(
            f"RAW TRADES | "
            f"{symbol} | "
            f"{len(raw_trades)}"
        )

        all_raw.extend(
            raw_trades
        )

    all_raw.sort(
        key=trade_time
    )

    (
        validation_start,
        holdout_start,
    ) = split_boundaries(
        all_raw
    )

    (
        dev_raw,
        val_raw,
        holdout_raw,
    ) = split_raw(
        all_raw,
        validation_start,
        holdout_start,
    )

    print()

    print(
        "VALIDATION START: "
        f"{format_time(validation_start)}"
    )

    print(
        "HOLDOUT START: "
        f"{format_time(holdout_start)}"
    )

    print(
        "HOLDOUT RAW TRADES: "
        f"{len(holdout_raw)} "
        "(LOCKED)"
    )

    print()

    print(
        "=" * 80
    )

    print(
        "CANDIDATE TESTS | "
        "DEV vs VALIDATION"
    )

    print(
        "=" * 80
    )

    print(
        f"{'CANDIDATE':<28} "
        f"{'DEV N':>6} "
        f"{'D PF':>6} "
        f"{'D NET':>9} "
        f"{'VAL N':>6} "
        f"{'V PF':>6} "
        f"{'V NET':>9} "
        f"{'RESULT':>9}"
    )

    robust = []

    for (
        name,
        predicate,
    ) in candidates():
        dev = (
            filtered_one_open(
                dev_raw,
                predicate,
            )
        )

        val = (
            filtered_one_open(
                val_raw,
                predicate,
            )
        )

        d = stats(
            dev
        )

        v = stats(
            val
        )

        ok = passes(
            d,
            v,
        )

        print(
            f"{name:<28} "
            f"{d['n']:>6} "
            f"{pf_text(d['pf']):>6} "
            f"{d['net']:>+9.1f} "
            f"{v['n']:>6} "
            f"{pf_text(v['pf']):>6} "
            f"{v['net']:>+9.1f} "
            f"{('PASS' if ok else 'REJECT'):>9}"
        )

        if ok:
            robust.append(
                (
                    name,
                    d,
                    v,
                )
            )

    robust.sort(
        key=lambda item: (
            item[2]["pf"],
            item[2]["net"],
        ),
        reverse=True,
    )

    print()

    print(
        "=" * 80
    )

    print(
        "ROBUST CANDIDATES: "
        f"{len(robust)}"
    )

    print(
        "=" * 80
    )

    if not robust:
        print(
            "NONE"
        )

        print(
            "No predefined EMA/RSI "
            "filter was profitable "
            "in both DEV and "
            "VALIDATION."
        )

    else:
        for (
            index,
            (
                name,
                d,
                v,
            ),
        ) in enumerate(
            robust,
            start=1,
        ):
            print(
                f"{index}. "
                f"{name} | "
                f"DEV N="
                f"{d['n']} "
                f"PF="
                f"{pf_text(d['pf'])} "
                f"NET="
                f"{d['net']:+.1f} | "
                f"VAL N="
                f"{v['n']} "
                f"PF="
                f"{pf_text(v['pf'])} "
                f"NET="
                f"{v['net']:+.1f}"
            )

    print()

    print(
        "HOLDOUT STATUS: LOCKED"
    )

    print(
        "The final 30% was not "
        "evaluated by any candidate."
    )

    print()

    print(
        "RESEARCH V2 COMPLETE"
    )


if __name__ == "__main__":
    main()
