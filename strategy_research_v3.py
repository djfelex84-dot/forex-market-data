import math
import requests

from bisect import bisect_right
from datetime import timedelta


RESEARCH_V2_URL = (
    "https://raw.githubusercontent.com/djfelex84-dot/"
    "forex-market-data/main/strategy_research_v2.py"
)

DEV_RATIO = 0.50
VALIDATION_RATIO = 0.20

MIN_DEV_TRADES = 40
MIN_VALIDATION_TRADES = 15


def load_research_v2():
    response = requests.get(
        RESEARCH_V2_URL,
        timeout=30,
    )

    response.raise_for_status()

    namespace = {
        "__name__":
            "strategy_research_v2_import"
    }

    exec(
        compile(
            response.text,
            "strategy_research_v2.py",
            "exec",
        ),
        namespace,
    )

    return namespace


R = load_research_v2()

SYMBOLS = R["SYMBOLS"]
INTERVAL = R["INTERVAL"]
INTERVAL_DELTA = R[
    "INTERVAL_DELTA"
]

fetch_historical_candles = R[
    "fetch_historical_candles"
]

parse_utc_datetime = R[
    "parse_utc_datetime"
]

format_time = R[
    "format_time"
]

is_forex_session_open = R[
    "is_forex_session_open"
]

build_trade_v2 = R[
    "build_trade_v2"
]

apply_one_open = R[
    "apply_one_open"
]

stats = R["stats"]
pf_text = R["pf_text"]
trade_time = R["trade_time"]


def ema(values, period):
    result = [
        None
    ] * len(values)

    if len(values) < period:
        return result

    previous = (
        sum(
            values[:period]
        )
        / period
    )

    result[
        period - 1
    ] = previous

    multiplier = (
        2.0
        / (
            period + 1
        )
    )

    for index in range(
        period,
        len(values),
    ):
        previous = (
            (
                values[index]
                - previous
            )
            * multiplier
            + previous
        )

        result[index] = (
            previous
        )

    return result


def atr(candles, period=14):
    result = [
        None
    ] * len(candles)

    if len(candles) <= period:
        return result

    true_ranges = [
        None
    ]

    for index in range(
        1,
        len(candles),
    ):
        high = (
            candles[index][
                "high"
            ]
        )

        low = (
            candles[index][
                "low"
            ]
        )

        previous_close = (
            candles[
                index - 1
            ]["close"]
        )

        true_ranges.append(
            max(
                high - low,
                abs(
                    high
                    - previous_close
                ),
                abs(
                    low
                    - previous_close
                ),
            )
        )

    previous = (
        sum(
            true_ranges[
                1:
                period + 1
            ]
        )
        / period
    )

    result[period] = (
        previous
    )

    for index in range(
        period + 1,
        len(candles),
    ):
        previous = (
            (
                previous
                * (
                    period - 1
                )
            )
            + true_ranges[index]
        ) / period

        result[index] = (
            previous
        )

    return result


def rsi_value(
    average_gain,
    average_loss,
):
    if average_loss == 0:
        if average_gain > 0:
            return 100.0

        return 50.0

    rs = (
        average_gain
        / average_loss
    )

    return (
        100.0
        - (
            100.0
            / (
                1.0 + rs
            )
        )
    )


def rsi(values, period=14):
    result = [
        None
    ] * len(values)

    if len(values) <= period:
        return result

    gains = []
    losses = []

    for index in range(
        1,
        period + 1,
    ):
        change = (
            values[index]
            - values[
                index - 1
            ]
        )

        gains.append(
            max(
                change,
                0.0,
            )
        )

        losses.append(
            max(
                -change,
                0.0,
            )
        )

    average_gain = (
        sum(gains)
        / period
    )

    average_loss = (
        sum(losses)
        / period
    )

    result[period] = (
        rsi_value(
            average_gain,
            average_loss,
        )
    )

    for index in range(
        period + 1,
        len(values),
    ):
        change = (
            values[index]
            - values[
                index - 1
            ]
        )

        gain = max(
            change,
            0.0,
        )

        loss = max(
            -change,
            0.0,
        )

        average_gain = (
            (
                average_gain
                * (
                    period - 1
                )
            )
            + gain
        ) / period

        average_loss = (
            (
                average_loss
                * (
                    period - 1
                )
            )
            + loss
        ) / period

        result[index] = (
            rsi_value(
                average_gain,
                average_loss,
            )
        )

    return result


def enrich(candles):
    closes = [
        candle["close"]
        for candle in candles
    ]

    ema20 = ema(
        closes,
        20,
    )

    ema50 = ema(
        closes,
        50,
    )

    atr14 = atr(
        candles,
        14,
    )

    rsi14 = rsi(
        closes,
        14,
    )

    result = []

    for index, candle in enumerate(
        candles
    ):
        row = dict(
            candle
        )

        row["ema20"] = (
            ema20[index]
        )

        row["ema50"] = (
            ema50[index]
        )

        row["atr14"] = (
            atr14[index]
        )

        row["rsi14"] = (
            rsi14[index]
        )

        result.append(
            row
        )

    return result


def floor_15m(value):
    minute = (
        value.minute
        - (
            value.minute
            % 15
        )
    )

    return value.replace(
        minute=minute,
        second=0,
        microsecond=0,
    )


def build_15m(candles):
    buckets = {}

    for candle in candles:
        candle_time = (
            parse_utc_datetime(
                candle[
                    "datetime"
                ]
            )
        )

        key = (
            format_time(
                floor_15m(
                    candle_time
                )
            )
        )

        buckets.setdefault(
            key,
            [],
        ).append(
            candle
        )

    result = []

    for key in sorted(
        buckets
    ):
        group = sorted(
            buckets[key],
            key=lambda candle:
                parse_utc_datetime(
                    candle[
                        "datetime"
                    ]
                ),
        )

        if len(group) != 3:
            continue

        times = [
            parse_utc_datetime(
                candle[
                    "datetime"
                ]
            )
            for candle in group
        ]

        if (
            times[1]
            - times[0]
            != INTERVAL_DELTA
        ):
            continue

        if (
            times[2]
            - times[1]
            != INTERVAL_DELTA
        ):
            continue

        result.append(
            {
                "datetime":
                    key,

                "open":
                    group[0][
                        "open"
                    ],

                "high":
                    max(
                        candle[
                            "high"
                        ]
                        for candle
                        in group
                    ),

                "low":
                    min(
                        candle[
                            "low"
                        ]
                        for candle
                        in group
                    ),

                "close":
                    group[-1][
                        "close"
                    ],
            }
        )

    result = enrich(
        result
    )

    for candle in result:
        candle[
            "close_time"
        ] = (
            parse_utc_datetime(
                candle[
                    "datetime"
                ]
            )
            + timedelta(
                minutes=15
            )
        )

    return result


def candle_to_ema_distance(
    candle,
    ema_value,
    atr_value,
):
    if (
        ema_value is None
        or atr_value is None
        or atr_value <= 0
    ):
        return None

    if (
        candle["low"]
        <= ema_value
        <= candle["high"]
    ):
        return 0.0

    return (
        min(
            abs(
                candle["low"]
                - ema_value
            ),
            abs(
                candle["high"]
                - ema_value
            ),
        )
        / atr_value
    )


def make_setup(
    candles_5m,
    index,
    candles_15m,
    index_15m,
):
    if (
        index < 1
        or index_15m < 1
    ):
        return None

    current = (
        candles_5m[
            index
        ]
    )

    previous = (
        candles_5m[
            index - 1
        ]
    )

    trend = (
        candles_15m[
            index_15m
        ]
    )

    trend_previous = (
        candles_15m[
            index_15m - 1
        ]
    )

    required = [
        current["ema20"],
        current["atr14"],
        current["rsi14"],
        previous["ema20"],
        previous["atr14"],
        trend["ema20"],
        trend["ema50"],
        trend["atr14"],
        trend_previous[
            "ema20"
        ],
    ]

    if any(
        value is None
        for value in required
    ):
        return None

    if (
        current["atr14"] <= 0
        or trend["atr14"] <= 0
    ):
        return None

    signal_time = (
        parse_utc_datetime(
            current[
                "datetime"
            ]
        )
        + INTERVAL_DELTA
    )

    if not (
        is_forex_session_open(
            format_time(
                signal_time
            )
        )
    ):
        return None

    separation = (
        abs(
            trend["ema20"]
            - trend["ema50"]
        )
        / trend["atr14"]
    )

    slope = (
        (
            trend["ema20"]
            - trend_previous[
                "ema20"
            ]
        )
        / trend["atr14"]
    )

    if (
        trend["ema20"]
        > trend["ema50"]
        and slope > 0
    ):
        signal = "BUY"

    elif (
        trend["ema20"]
        < trend["ema50"]
        and slope < 0
    ):
        signal = "SELL"

    else:
        return None

    pullback = (
        candle_to_ema_distance(
            previous,
            previous["ema20"],
            previous["atr14"],
        )
    )

    if pullback is None:
        return None

    body_atr = (
        abs(
            current["close"]
            - current["open"]
        )
        / current["atr14"]
    )

    rsi_5m = (
        current["rsi14"]
    )

    if signal == "BUY":
        confirmation = (
            current["close"]
            > current["open"]
            and
            current["close"]
            > current["ema20"]
        )

        breakout = (
            current["close"]
            > previous["high"]
        )

        rsi_wide = (
            50
            <= rsi_5m
            <= 68
        )

        rsi_core = (
            52
            <= rsi_5m
            <= 64
        )

    else:
        confirmation = (
            current["close"]
            < current["open"]
            and
            current["close"]
            < current["ema20"]
        )

        breakout = (
            current["close"]
            < previous["low"]
        )

        rsi_wide = (
            32
            <= rsi_5m
            <= 50
        )

        rsi_core = (
            36
            <= rsi_5m
            <= 48
        )

    if not confirmation:
        return None

    return {
        "signal":
            signal,

        "datetime":
            current[
                "datetime"
            ],

        "close":
            current[
                "close"
            ],

        "atr":
            current[
                "atr14"
            ],

        "signal_time":
            signal_time,

        "ema15_sep":
            separation,

        "ema15_slope":
            abs(
                slope
            ),

        "pullback":
            pullback,

        "body_atr":
            body_atr,

        "breakout":
            breakout,

        "rsi_wide":
            rsi_wide,

        "rsi_core":
            rsi_core,
    }


def candidates():
    return [
        {
            "name":
                "MTF_BASIC",

            "min_sep":
                0.30,

            "min_slope":
                0.00,

            "max_pullback":
                0.35,

            "breakout":
                False,

            "min_body":
                0.00,

            "rsi":
                "WIDE",

            "hours":
                None,
        },

        {
            "name":
                "MTF_BREAKOUT",

            "min_sep":
                0.30,

            "min_slope":
                0.00,

            "max_pullback":
                0.35,

            "breakout":
                True,

            "min_body":
                0.00,

            "rsi":
                "WIDE",

            "hours":
                None,
        },

        {
            "name":
                "MTF_STRONG",

            "min_sep":
                0.50,

            "min_slope":
                0.05,

            "max_pullback":
                0.30,

            "breakout":
                True,

            "min_body":
                0.20,

            "rsi":
                "CORE",

            "hours":
                None,
        },

        {
            "name":
                "MTF_STRONG_00_16",

            "min_sep":
                0.50,

            "min_slope":
                0.05,

            "max_pullback":
                0.30,

            "breakout":
                True,

            "min_body":
                0.20,

            "rsi":
                "CORE",

            "hours":
                (
                    0,
                    16,
                ),
        },

        {
            "name":
                "MTF_PULLBACK_00_16",

            "min_sep":
                0.60,

            "min_slope":
                0.08,

            "max_pullback":
                0.25,

            "breakout":
                False,

            "min_body":
                0.15,

            "rsi":
                "CORE",

            "hours":
                (
                    0,
                    16,
                ),
        },
    ]


def matches(
    setup,
    candidate,
):
    if (
        setup["ema15_sep"]
        < candidate[
            "min_sep"
        ]
    ):
        return False

    if (
        setup["ema15_slope"]
        < candidate[
            "min_slope"
        ]
    ):
        return False

    if (
        setup["pullback"]
        > candidate[
            "max_pullback"
        ]
    ):
        return False

    if (
        candidate["breakout"]
        and
        not setup["breakout"]
    ):
        return False

    if (
        setup["body_atr"]
        < candidate[
            "min_body"
        ]
    ):
        return False

    if (
        candidate["rsi"]
        == "WIDE"
        and
        not setup["rsi_wide"]
    ):
        return False

    if (
        candidate["rsi"]
        == "CORE"
        and
        not setup["rsi_core"]
    ):
        return False

    hours = (
        candidate["hours"]
    )

    if hours is not None:
        start_hour = (
            hours[0]
        )

        end_hour = (
            hours[1]
        )

        hour = (
            setup[
                "signal_time"
            ].hour
        )

        if not (
            start_hour
            <= hour
            < end_hour
        ):
            return False

    return True


def generate_trades(
    symbol,
    candles,
    candidate,
):
    candles_5m = enrich(
        candles
    )

    candles_15m = (
        build_15m(
            candles
        )
    )

    close_times_15m = [
        candle[
            "close_time"
        ]
        for candle
        in candles_15m
    ]

    trades = []

    for index in range(
        1,
        len(candles_5m),
    ):
        signal_time = (
            parse_utc_datetime(
                candles_5m[
                    index
                ]["datetime"]
            )
            + INTERVAL_DELTA
        )

        index_15m = (
            bisect_right(
                close_times_15m,
                signal_time,
            )
            - 1
        )

        if index_15m < 1:
            continue

        setup = (
            make_setup(
                candles_5m,
                index,
                candles_15m,
                index_15m,
            )
        )

        if setup is None:
            continue

        if not matches(
            setup,
            candidate,
        ):
            continue

        result = {
            "datetime":
                setup[
                    "datetime"
                ],

            "signal":
                setup[
                    "signal"
                ],

            "close":
                setup[
                    "close"
                ],

            "atr":
                setup[
                    "atr"
                ],

            "setup_score":
                None,

            "rsi":
                None,

            "ema_distance_atr":
                None,
        }

        trade = (
            build_trade_v2(
                symbol,
                result,
                candles_5m,
                index,
            )
        )

        if trade is not None:
            trades.append(
                trade
            )

    return trades


def positive_rate(trades):
    resolved = [
        trade
        for trade in trades
        if trade[
            "net_pnl_pips"
        ] is not None
    ]

    if not resolved:
        return 0.0

    positive = sum(
        1
        for trade in resolved
        if trade[
            "net_pnl_pips"
        ] > 0
    )

    return (
        positive
        / len(resolved)
        * 100
    )


def split_trades(
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


def passes(
    dev_stats,
    validation_stats,
):
    return (
        dev_stats["n"]
        >= MIN_DEV_TRADES

        and
        validation_stats["n"]
        >= MIN_VALIDATION_TRADES

        and
        dev_stats["pf"]
        > 1.0

        and
        validation_stats["pf"]
        > 1.0

        and
        dev_stats["net"]
        > 0

        and
        validation_stats["net"]
        > 0

        and
        dev_stats["avg_r"]
        > 0

        and
        validation_stats["avg_r"]
        > 0
    )


def main():
    print(
        "AS STRATEGY RESEARCH V3"
    )

    print(
        f"Interval: {INTERVAL}"
    )

    print(
        "Architecture: "
        "15m trend + "
        "5m pullback + "
        "5m confirmation"
    )

    print(
        "History: "
        "20000 candles per symbol"
    )

    print(
        "Split: "
        "50% DEV / "
        "20% VALIDATION / "
        "30% HOLDOUT"
    )

    print(
        "HOLDOUT: LOCKED"
    )

    print(
        "Live bot / Telegram / "
        "SQLite are NOT modified."
    )

    symbol_data = {}

    starts = []
    ends = []

    for symbol in SYMBOLS:
        print()

        print(
            f"FETCH | {symbol}"
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

        symbol_data[
            symbol
        ] = candles

        starts.append(
            parse_utc_datetime(
                candles[0][
                    "datetime"
                ]
            )
        )

        ends.append(
            parse_utc_datetime(
                candles[-1][
                    "datetime"
                ]
            )
            + INTERVAL_DELTA
        )

        print(
            f"DATA | {symbol} | "
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

    start_time = max(
        starts
    )

    end_time = min(
        ends
    )

    span = (
        end_time
        - start_time
    )

    validation_start = (
        start_time
        + span
        * DEV_RATIO
    )

    holdout_start = (
        start_time
        + span
        * (
            DEV_RATIO
            + VALIDATION_RATIO
        )
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

    print()

    print(
        "=" * 96
    )

    print(
        "V3 CANDIDATES | "
        "DEV vs VALIDATION"
    )

    print(
        "=" * 96
    )

    print(
        f"{'CANDIDATE':<22} "
        f"{'DEV N':>6} "
        f"{'D POS':>7} "
        f"{'D PF':>6} "
        f"{'D NET':>9} "
        f"{'VAL N':>6} "
        f"{'V POS':>7} "
        f"{'V PF':>6} "
        f"{'V NET':>9} "
        f"{'RESULT':>9}"
    )

    robust = []

    for candidate in (
        candidates()
    ):
        all_trades = []

        for symbol in SYMBOLS:
            all_trades.extend(
                generate_trades(
                    symbol,
                    symbol_data[
                        symbol
                    ],
                    candidate,
                )
            )

        all_trades = (
            apply_one_open(
                all_trades
            )
        )

        (
            dev,
            validation,
            holdout,
        ) = (
            split_trades(
                all_trades,
                validation_start,
                holdout_start,
            )
        )

        dev_stats = stats(
            dev
        )

        val_stats = stats(
            validation
        )

        dev_positive = (
            positive_rate(
                dev
            )
        )

        val_positive = (
            positive_rate(
                validation
            )
        )

        passed = passes(
            dev_stats,
            val_stats,
        )

        print(
            f"{candidate['name']:<22} "
            f"{dev_stats['n']:>6} "
            f"{dev_positive:>6.1f}% "
            f"{pf_text(dev_stats['pf']):>6} "
            f"{dev_stats['net']:>+9.1f} "
            f"{val_stats['n']:>6} "
            f"{val_positive:>6.1f}% "
            f"{pf_text(val_stats['pf']):>6} "
            f"{val_stats['net']:>+9.1f} "
            f"{('PASS' if passed else 'REJECT'):>9}"
        )

        if passed:
            robust.append(
                (
                    candidate[
                        "name"
                    ],
                    dev_stats,
                    val_stats,
                    len(
                        holdout
                    ),
                )
            )

    print()

    print(
        "=" * 96
    )

    print(
        "ROBUST V3 CANDIDATES: "
        f"{len(robust)}"
    )

    print(
        "=" * 96
    )

    if not robust:
        print(
            "NONE"
        )

        print(
            "This first "
            "multi-timeframe family "
            "did not prove an edge "
            "in both DEV and "
            "VALIDATION."
        )

    else:
        robust.sort(
            key=lambda item: (
                item[2]["pf"],
                item[2]["net"],
            ),
            reverse=True,
        )

        for (
            index,
            (
                name,
                dev_stats,
                val_stats,
                holdout_count,
            ),
        ) in enumerate(
            robust,
            start=1,
        ):
            print(
                f"{index}. "
                f"{name} | "
                f"DEV N="
                f"{dev_stats['n']} "
                f"PF="
                f"{pf_text(dev_stats['pf'])} "
                f"NET="
                f"{dev_stats['net']:+.1f} | "
                f"VAL N="
                f"{val_stats['n']} "
                f"PF="
                f"{pf_text(val_stats['pf'])} "
                f"NET="
                f"{val_stats['net']:+.1f} | "
                f"HOLDOUT TRADES="
                f"{holdout_count} "
                "(NOT EVALUATED)"
            )

    print()

    print(
        "HOLDOUT STATUS: LOCKED"
    )

    print(
        "No holdout P/L or "
        "profit factor was "
        "calculated."
    )

    print()

    print(
        "RESEARCH V3 COMPLETE"
    )


if __name__ == "__main__":
    main()
