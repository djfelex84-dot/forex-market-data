import os
import sqlite3

from datetime import (
    datetime,
    timedelta,
)

from strategy import (
    analyze_market,
)


DB_PATH = os.getenv(
    "DB_PATH",
    "/app/data/trading.db",
)


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

RESEARCH_INTERVAL = "15min"

SOURCE_INTERVAL_MINUTES = 5

RESEARCH_INTERVAL_MINUTES = 15

MIN_RESEARCH_CANDLES = 60

MAX_RESEARCH_CANDLES = 120


def get_connection():
    connection = sqlite3.connect(
        DB_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    return connection


def init_15m_research_tables():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            research_15m_candles (
                id INTEGER
                PRIMARY KEY
                AUTOINCREMENT,

                symbol TEXT
                NOT NULL,

                candle_time TEXT
                NOT NULL,

                open REAL
                NOT NULL,

                high REAL
                NOT NULL,

                low REAL
                NOT NULL,

                close REAL
                NOT NULL,

                UNIQUE (
                    symbol,
                    candle_time
                )
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            research_15m_analysis (
                id INTEGER
                PRIMARY KEY
                AUTOINCREMENT,

                symbol TEXT
                NOT NULL,

                interval TEXT
                NOT NULL,

                candle_time TEXT
                NOT NULL,

                close REAL
                NOT NULL,

                ema_fast REAL
                NOT NULL,

                ema_slow REAL
                NOT NULL,

                rsi REAL
                NOT NULL,

                atr REAL
                NOT NULL,

                ema_distance_atr REAL
                NOT NULL,

                ema_direction TEXT
                NOT NULL,

                trend TEXT
                NOT NULL,

                candidate TEXT
                NOT NULL,

                signal TEXT
                NOT NULL,

                status TEXT
                NOT NULL,

                setup_score INTEGER
                NOT NULL,

                reason TEXT
                NOT NULL,

                UNIQUE (
                    symbol,
                    interval,
                    candle_time
                )
            )
            """
        )

        connection.commit()


def floor_to_15m(
    candle_time
):
    minute = (
        candle_time.minute
        // RESEARCH_INTERVAL_MINUTES
        * RESEARCH_INTERVAL_MINUTES
    )

    return candle_time.replace(
        minute=minute,
        second=0,
        microsecond=0,
    )


def aggregate_5m_to_15m(
    five_minute_candles
):
    groups = {}

    for candle in five_minute_candles:
        try:
            candle_time = (
                datetime.strptime(
                    candle[
                        "datetime"
                    ],
                    TIME_FORMAT,
                )
            )

            group_time = (
                floor_to_15m(
                    candle_time
                )
            )

            group_key = (
                group_time.strftime(
                    TIME_FORMAT
                )
            )

            if group_key not in groups:
                groups[
                    group_key
                ] = []

            groups[
                group_key
            ].append(
                {
                    "datetime":
                        candle[
                            "datetime"
                        ],

                    "open":
                        float(
                            candle[
                                "open"
                            ]
                        ),

                    "high":
                        float(
                            candle[
                                "high"
                            ]
                        ),

                    "low":
                        float(
                            candle[
                                "low"
                            ]
                        ),

                    "close":
                        float(
                            candle[
                                "close"
                            ]
                        ),
                }
            )

        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            continue

    aggregated = []

    for (
        group_time_text,
        candles,
    ) in groups.items():

        if len(candles) != 3:
            continue

        candles.sort(
            key=lambda item:
                item[
                    "datetime"
                ]
        )

        group_time = (
            datetime.strptime(
                group_time_text,
                TIME_FORMAT,
            )
        )

        expected_times = [
            (
                group_time
                + timedelta(
                    minutes=offset
                )
            ).strftime(
                TIME_FORMAT
            )
            for offset in (
                0,
                5,
                10,
            )
        ]

        actual_times = [
            candle[
                "datetime"
            ]
            for candle in candles
        ]

        if (
            actual_times
            != expected_times
        ):
            continue

        aggregated.append(
            {
                "datetime":
                    group_time_text,

                "open":
                    candles[
                        0
                    ][
                        "open"
                    ],

                "high":
                    max(
                        candle[
                            "high"
                        ]
                        for candle
                        in candles
                    ),

                "low":
                    min(
                        candle[
                            "low"
                        ]
                        for candle
                        in candles
                    ),

                "close":
                    candles[
                        -1
                    ][
                        "close"
                    ],
            }
        )

    aggregated.sort(
        key=lambda item:
            item[
                "datetime"
            ]
    )

    return aggregated


def save_15m_candles(
    symbol,
    candles
):
    saved_count = 0

    with get_connection() as connection:
        for candle in candles:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO
                research_15m_candles (
                    symbol,
                    candle_time,
                    open,
                    high,
                    low,
                    close
                )

                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,

                    candle[
                        "datetime"
                    ],

                    candle[
                        "open"
                    ],

                    candle[
                        "high"
                    ],

                    candle[
                        "low"
                    ],

                    candle[
                        "close"
                    ],
                ),
            )

            if (
                cursor.rowcount
                > 0
            ):
                saved_count += 1

        connection.commit()

    return saved_count


def count_15m_candles(
    symbol
):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total

            FROM research_15m_candles

            WHERE symbol = ?
            """,
            (
                symbol,
            ),
        ).fetchone()

    return int(
        row[
            "total"
        ]
        or 0
    )


def get_15m_candles(
    symbol,
    limit=MAX_RESEARCH_CANDLES,
):
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                candle_time,
                open,
                high,
                low,
                close

            FROM research_15m_candles

            WHERE symbol = ?

            ORDER BY
                candle_time DESC

            LIMIT ?
            """,
            (
                symbol,
                limit,
            ),
        ).fetchall()

    candles = []

    for row in reversed(
        rows
    ):
        candles.append(
            {
                "datetime":
                    row[
                        "candle_time"
                    ],

                "open":
                    float(
                        row[
                            "open"
                        ]
                    ),

                "high":
                    float(
                        row[
                            "high"
                        ]
                    ),

                "low":
                    float(
                        row[
                            "low"
                        ]
                    ),

                "close":
                    float(
                        row[
                            "close"
                        ]
                    ),
            }
        )

    return candles


def save_15m_analysis(
    symbol,
    result
):
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO
            research_15m_analysis (
                symbol,
                interval,
                candle_time,
                close,
                ema_fast,
                ema_slow,
                rsi,
                atr,
                ema_distance_atr,
                ema_direction,
                trend,
                candidate,
                signal,
                status,
                setup_score,
                reason
            )

            VALUES (
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?
            )
            """,
            (
                symbol,

                RESEARCH_INTERVAL,

                result[
                    "datetime"
                ],

                result[
                    "close"
                ],

                result[
                    "ema_fast"
                ],

                result[
                    "ema_slow"
                ],

                result[
                    "rsi"
                ],

                result[
                    "atr"
                ],

                result[
                    "ema_distance_atr"
                ],

                result[
                    "ema_direction"
                ],

                result[
                    "trend"
                ],

                result[
                    "candidate"
                ],

                result[
                    "signal"
                ],

                result[
                    "status"
                ],

                result[
                    "setup_score"
                ],

                result[
                    "reason"
                ],
            ),
        )

        connection.commit()

    return (
        cursor.rowcount
        > 0
    )


def format_15m_result(
    symbol,
    result
):
    return (
        "15M RESEARCH | "
        f"{symbol} | "

        f"Candle="
        f"{result['datetime']} | "

        f"Close="
        f"{result['close']:.5f} | "

        f"EMA20="
        f"{result['ema_fast']:.5f} | "

        f"EMA50="
        f"{result['ema_slow']:.5f} | "

        f"RSI14="
        f"{result['rsi']:.2f} | "

        f"ATR14="
        f"{result['atr']:.5f} | "

        f"EMA-distance="
        f"{result['ema_distance_atr']:.2f} ATR | "

        f"EMA-direction="
        f"{result['ema_direction']} | "

        f"Trend="
        f"{result['trend']} | "

        f"Candidate="
        f"{result['candidate']} | "

        f"Signal="
        f"{result['signal']} | "

        f"Status="
        f"{result['status']} | "

        f"SetupScore="
        f"{result['setup_score']}/100 | "

        f"{result['reason']}"
    )


def process_15m_research(
    symbol,
    five_minute_candles,
):
    init_15m_research_tables()

    aggregated = (
        aggregate_5m_to_15m(
            five_minute_candles
        )
    )

    saved_count = (
        save_15m_candles(
            symbol,
            aggregated,
        )
    )

    total_candles = (
        count_15m_candles(
            symbol
        )
    )

    if (
        total_candles
        < MIN_RESEARCH_CANDLES
    ):
        if saved_count > 0:
            print(
                "15M RESEARCH COLLECTING | "
                f"{symbol} | "
                f"Candles="
                f"{total_candles}/"
                f"{MIN_RESEARCH_CANDLES} | "
                f"New="
                f"{saved_count}",
                flush=True,
            )

        return None

    # Do not repeat analysis every
    # 5-minute cycle when no new
    # complete 15-minute candle
    # was added.
    if saved_count == 0:
        return None

    candles = (
        get_15m_candles(
            symbol
        )
    )

    result = (
        analyze_market(
            candles,
            symbol,
        )
    )

    saved_analysis = (
        save_15m_analysis(
            symbol,
            result,
        )
    )

    if not saved_analysis:
        return None

    print(
        format_15m_result(
            symbol,
            result,
        ),
        flush=True,
    )

    return result
