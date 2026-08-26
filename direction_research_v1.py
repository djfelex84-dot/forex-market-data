import statistics
import requests

from bisect import bisect_right


RESEARCH_V3_URL = (
    "https://raw.githubusercontent.com/djfelex84-dot/"
    "forex-market-data/main/strategy_research_v3.py"
)

HORIZONS_MINUTES = (15, 30, 60, 120, 180)
DEV_RATIO = 0.50
VALIDATION_RATIO = 0.20


def load_research_v3():
    response = requests.get(
        RESEARCH_V3_URL,
        timeout=30,
    )

    response.raise_for_status()

    namespace = {
        "__name__":
            "strategy_research_v3_import"
    }

    exec(
        compile(
            response.text,
            "strategy_research_v3.py",
            "exec",
        ),
        namespace,
    )

    return namespace


V3 = load_research_v3()

SYMBOLS = V3["SYMBOLS"]
INTERVAL = V3["INTERVAL"]
INTERVAL_DELTA = V3[
    "INTERVAL_DELTA"
]

fetch_historical_candles = V3[
    "fetch_historical_candles"
]

parse_utc_datetime = V3[
    "parse_utc_datetime"
]

format_time = V3[
    "format_time"
]

enrich = V3[
    "enrich"
]

build_15m = V3[
    "build_15m"
]

make_setup = V3[
    "make_setup"
]

matches = V3[
    "matches"
]

v3_candidates = V3[
    "candidates"
]

get_instrument_config = V3[
    "R"
]["get_instrument_config"]


def get_basic_candidate():
    for candidate in v3_candidates():
        if (
            candidate["name"]
            == "MTF_BASIC"
        ):
            return candidate

    raise RuntimeError(
        "MTF_BASIC candidate "
        "not found"
    )


BASIC_CANDIDATE = (
    get_basic_candidate()
)


def build_setups(
    symbol,
    candles,
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

    setups_all = []
    setups_basic = []

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

        row = {
            "symbol":
                symbol,

            "index":
                index,

            "signal":
                setup[
                    "signal"
                ],

            "signal_time":
                setup[
                    "signal_time"
                ],

            "entry_price":
                setup[
                    "close"
                ],
        }

        setups_all.append(
            row
        )

        if matches(
            setup,
            BASIC_CANDIDATE,
        ):
            setups_basic.append(
                row
            )

    return (
        candles_5m,
        setups_all,
        setups_basic,
    )


def is_continuous_window(
    candles,
    start_index,
    end_index,
):
    if end_index >= len(
        candles
    ):
        return False

    previous_time = (
        parse_utc_datetime(
            candles[
                start_index
            ]["datetime"]
        )
    )

    for index in range(
        start_index + 1,
        end_index + 1,
    ):
        current_time = (
            parse_utc_datetime(
                candles[
                    index
                ]["datetime"]
            )
        )

        if (
            current_time
            - previous_time
            != INTERVAL_DELTA
        ):
            return False

        previous_time = (
            current_time
        )

    return True


def direction_metrics(
    symbol,
    candles,
    setup,
    horizon_minutes,
):
    interval_minutes = (
        INTERVAL_DELTA
        .total_seconds()
        / 60
    )

    steps = int(
        horizon_minutes
        / interval_minutes
    )

    start_index = (
        setup[
            "index"
        ]
    )

    end_index = (
        start_index
        + steps
    )

    if not (
        is_continuous_window(
            candles,
            start_index,
            end_index,
        )
    ):
        return None

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

    entry = (
        setup[
            "entry_price"
        ]
    )

    exit_price = (
        candles[
            end_index
        ]["close"]
    )

    future = candles[
        start_index + 1:
        end_index + 1
    ]

    if (
        setup["signal"]
        == "BUY"
    ):
        trend_move = (
            exit_price
            - entry
        ) / pip_size

        trend_mfe = (
            max(
                candle[
                    "high"
                ]
                for candle
                in future
            )
            - entry
        ) / pip_size

        trend_mae = (
            entry
            - min(
                candle[
                    "low"
                ]
                for candle
                in future
            )
        ) / pip_size

    else:
        trend_move = (
            entry
            - exit_price
        ) / pip_size

        trend_mfe = (
            entry
            - min(
                candle[
                    "low"
                ]
                for candle
                in future
            )
        ) / pip_size

        trend_mae = (
            max(
                candle[
                    "high"
                ]
                for candle
                in future
            )
            - entry
        ) / pip_size

    trend_mfe = max(
        0.0,
        trend_mfe,
    )

    trend_mae = max(
        0.0,
        trend_mae,
    )

    return {
        "trend_move":
            trend_move,

        "trend_mfe":
            trend_mfe,

        "trend_mae":
            trend_mae,

        "reverse_move":
            -trend_move,

        "reverse_mfe":
            trend_mae,

        "reverse_mae":
            trend_mfe,
    }


def split_boundaries(
    symbol_data,
):
    starts = []
    ends = []

    for candles in (
        symbol_data.values()
    ):
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

    return (
        start_time,
        validation_start,
        holdout_start,
        end_time,
    )


def collect_observations(
    symbol_data,
    setup_data,
    period_start,
    period_end,
    horizon_minutes,
):
    observations = []

    for symbol in SYMBOLS:
        candles = (
            symbol_data[
                symbol
            ]
        )

        setups = (
            setup_data.get(
                symbol,
                [],
            )
        )

        for setup in setups:
            signal_time = (
                setup[
                    "signal_time"
                ]
            )

            if not (
                period_start
                <= signal_time
                < period_end
            ):
                continue

            target_time = (
                signal_time
                + (
                    INTERVAL_DELTA
                    * int(
                        horizon_minutes
                        / (
                            INTERVAL_DELTA
                            .total_seconds()
                            / 60
                        )
                    )
                )
            )

            if (
                target_time
                >= period_end
            ):
                continue

            metrics = (
                direction_metrics(
                    symbol,
                    candles,
                    setup,
                    horizon_minutes,
                )
            )

            if metrics is None:
                continue

            observations.append(
                {
                    "symbol":
                        symbol,

                    "signal":
                        setup[
                            "signal"
                        ],

                    **metrics,
                }
            )

    return observations


def summarize(
    observations,
):
    if not observations:
        return {
            "n": 0,

            "trend_positive":
                0.0,

            "trend_avg":
                0.0,

            "trend_median":
                0.0,

            "trend_mfe":
                0.0,

            "trend_mae":
                0.0,

            "reverse_positive":
                0.0,

            "reverse_avg":
                0.0,

            "reverse_median":
                0.0,

            "reverse_mfe":
                0.0,

            "reverse_mae":
                0.0,
        }

    trend_moves = [
        row[
            "trend_move"
        ]
        for row
        in observations
    ]

    reverse_moves = [
        row[
            "reverse_move"
        ]
        for row
        in observations
    ]

    return {
        "n":
            len(
                observations
            ),

        "trend_positive":
            (
                sum(
                    1
                    for value
                    in trend_moves
                    if value > 0
                )
                / len(
                    trend_moves
                )
                * 100
            ),

        "trend_avg":
            statistics.mean(
                trend_moves
            ),

        "trend_median":
            statistics.median(
                trend_moves
            ),

        "trend_mfe":
            statistics.mean(
                row[
                    "trend_mfe"
                ]
                for row
                in observations
            ),

        "trend_mae":
            statistics.mean(
                row[
                    "trend_mae"
                ]
                for row
                in observations
            ),

        "reverse_positive":
            (
                sum(
                    1
                    for value
                    in reverse_moves
                    if value > 0
                )
                / len(
                    reverse_moves
                )
                * 100
            ),

        "reverse_avg":
            statistics.mean(
                reverse_moves
            ),

        "reverse_median":
            statistics.median(
                reverse_moves
            ),

        "reverse_mfe":
            statistics.mean(
                row[
                    "reverse_mfe"
                ]
                for row
                in observations
            ),

        "reverse_mae":
            statistics.mean(
                row[
                    "reverse_mae"
                ]
                for row
                in observations
            ),
    }


def print_table(
    title,
    symbol_data,
    setup_data,
    period_start,
    period_end,
):
    print()

    print(
        "=" * 132
    )

    print(
        title
    )

    print(
        "=" * 132
    )

    print(
        f"{'MIN':>5} "
        f"{'N':>6} | "
        f"{'TREND+':>7} "
        f"{'T AVG':>8} "
        f"{'T MED':>8} "
        f"{'T MFE':>8} "
        f"{'T MAE':>8} | "
        f"{'REV+':>7} "
        f"{'R AVG':>8} "
        f"{'R MED':>8} "
        f"{'R MFE':>8} "
        f"{'R MAE':>8}"
    )

    for horizon in (
        HORIZONS_MINUTES
    ):
        observations = (
            collect_observations(
                symbol_data,
                setup_data,
                period_start,
                period_end,
                horizon,
            )
        )

        result = (
            summarize(
                observations
            )
        )

        print(
            f"{horizon:>5} "
            f"{result['n']:>6} | "
            f"{result['trend_positive']:>6.1f}% "
            f"{result['trend_avg']:>+8.2f} "
            f"{result['trend_median']:>+8.2f} "
            f"{result['trend_mfe']:>8.2f} "
            f"{result['trend_mae']:>8.2f} | "
            f"{result['reverse_positive']:>6.1f}% "
            f"{result['reverse_avg']:>+8.2f} "
            f"{result['reverse_median']:>+8.2f} "
            f"{result['reverse_mfe']:>8.2f} "
            f"{result['reverse_mae']:>8.2f}"
        )


def filter_setup_data(
    setup_data,
    symbol=None,
    signal=None,
):
    result = {}

    for (
        key,
        setups,
    ) in setup_data.items():
        if (
            symbol is not None
            and key != symbol
        ):
            continue

        result[key] = [
            setup
            for setup
            in setups
            if (
                signal is None
                or
                setup[
                    "signal"
                ]
                == signal
            )
        ]

    return result


def print_period_research(
    period_name,
    symbol_data,
    setups_all,
    setups_basic,
    period_start,
    period_end,
):
    print()

    print(
        "#" * 132
    )

    print(
        f"{period_name} | "
        f"{format_time(period_start)} "
        f"-> "
        f"{format_time(period_end)}"
    )

    print(
        "#" * 132
    )

    print_table(
        f"{period_name} | "
        "MTF CONFIRM ALL | "
        "ALL SYMBOLS",
        symbol_data,
        setups_all,
        period_start,
        period_end,
    )

    print_table(
        f"{period_name} | "
        "MTF BASIC | "
        "ALL SYMBOLS",
        symbol_data,
        setups_basic,
        period_start,
        period_end,
    )

    for symbol in SYMBOLS:
        print_table(
            f"{period_name} | "
            f"MTF BASIC | "
            f"{symbol}",
            symbol_data,
            filter_setup_data(
                setups_basic,
                symbol=symbol,
            ),
            period_start,
            period_end,
        )

    print_table(
        f"{period_name} | "
        "MTF BASIC | BUY",
        symbol_data,
        filter_setup_data(
            setups_basic,
            signal="BUY",
        ),
        period_start,
        period_end,
    )

    print_table(
        f"{period_name} | "
        "MTF BASIC | SELL",
        symbol_data,
        filter_setup_data(
            setups_basic,
            signal="SELL",
        ),
        period_start,
        period_end,
    )


def main():
    print(
        "AS DIRECTION RESEARCH V1"
    )

    print(
        f"Interval: "
        f"{INTERVAL}"
    )

    print(
        "Question: does price move "
        "with the detected trend "
        "or against it?"
    )

    print(
        "Horizons: "
        "15 / 30 / 60 / "
        "120 / 180 minutes"
    )

    print(
        "Metrics: hit-rate, "
        "average move, median move, "
        "MFE, MAE"
    )

    print(
        "Moves are RAW pips "
        "before spread."
    )

    print(
        "No SL / TP optimization."
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
    setups_all = {}
    setups_basic = {}

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

        (
            candles_5m,
            symbol_all,
            symbol_basic,
        ) = (
            build_setups(
                symbol,
                candles,
            )
        )

        symbol_data[
            symbol
        ] = candles_5m

        setups_all[
            symbol
        ] = symbol_all

        setups_basic[
            symbol
        ] = symbol_basic

        print(
            f"DATA | {symbol} | "
            f"RawClosed="
            f"{raw_closed} | "
            f"SessionCandles="
            f"{len(candles_5m)} | "
            f"Excluded="
            f"{excluded} | "
            f"AllSetups="
            f"{len(symbol_all)} | "
            f"BasicSetups="
            f"{len(symbol_basic)} | "
            f"From="
            f"{candles_5m[0]['datetime']} | "
            f"To="
            f"{candles_5m[-1]['datetime']}"
        )

    (
        start_time,
        validation_start,
        holdout_start,
        end_time,
    ) = (
        split_boundaries(
            symbol_data
        )
    )

    print()

    print(
        "DEV START: "
        f"{format_time(start_time)}"
    )

    print(
        "VALIDATION START: "
        f"{format_time(validation_start)}"
    )

    print(
        "HOLDOUT START: "
        f"{format_time(holdout_start)}"
    )

    print(
        "DATA END: "
        f"{format_time(end_time)}"
    )

    print_period_research(
        "DEV",
        symbol_data,
        setups_all,
        setups_basic,
        start_time,
        validation_start,
    )

    print_period_research(
        "VALIDATION",
        symbol_data,
        setups_all,
        setups_basic,
        validation_start,
        holdout_start,
    )

    print()

    print(
        "=" * 132
    )

    print(
        "HOLDOUT STATUS: LOCKED"
    )

    print(
        "=" * 132
    )

    print(
        "The final 30% was "
        "not evaluated."
    )

    print()

    print(
        "INTERPRETATION GUIDE"
    )

    print(
        "TREND+ > 50% with "
        "positive T AVG and "
        "T MED in both DEV and "
        "VALIDATION suggests "
        "trend-following edge."
    )

    print(
        "REV+ > 50% with "
        "positive R AVG and "
        "R MED in both DEV and "
        "VALIDATION suggests "
        "mean-reversion edge."
    )

    print(
        "If neither side is stable "
        "across DEV and VALIDATION, "
        "EMA/RSI trend direction "
        "is not a useful base."
    )

    print()

    print(
        "DIRECTION RESEARCH V1 "
        "COMPLETE"
    )


if __name__ == "__main__":
    main()
