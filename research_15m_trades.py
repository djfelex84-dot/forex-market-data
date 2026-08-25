import os
import sqlite3
from datetime import datetime, timedelta, timezone

from config import (
    STOP_LOSS_ATR_MULTIPLIER,
    TAKE_PROFIT_R_MULTIPLE,
    MAX_TRADE_MINUTES,
    get_instrument_config,
)

DB_PATH = os.getenv(
    "DB_PATH",
    "/app/data/trading.db",
)

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

RESEARCH_INTERVAL = "15min"
RESEARCH_INTERVAL_MINUTES = 15
RESEARCH_MODEL_VERSION = "15M-V1"


def get_connection():
    connection = sqlite3.connect(
        DB_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    return connection


def init_15m_trade_tables():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            research_15m_signals (
                id INTEGER
                PRIMARY KEY
                AUTOINCREMENT,

                symbol TEXT
                NOT NULL,

                interval TEXT
                NOT NULL,

                candle_time TEXT
                NOT NULL,

                signal TEXT
                NOT NULL,

                entry_price REAL
                NOT NULL,

                atr REAL
                NOT NULL,

                setup_score INTEGER
                NOT NULL,

                created_at TEXT
                NOT NULL,

                UNIQUE (
                    symbol,
                    interval,
                    candle_time
                )
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            research_15m_trades (
                id INTEGER
                PRIMARY KEY
                AUTOINCREMENT,

                signal_id INTEGER
                NOT NULL
                UNIQUE,

                symbol TEXT
                NOT NULL,

                interval TEXT
                NOT NULL,

                signal TEXT
                NOT NULL,

                entry_candle_time TEXT
                NOT NULL,

                entry_price REAL
                NOT NULL,

                stop_loss REAL
                NOT NULL,

                take_profit REAL
                NOT NULL,

                risk_pips REAL
                NOT NULL,

                reward_pips REAL
                NOT NULL,

                spread_pips REAL
                NOT NULL,

                max_hold_minutes INTEGER
                NOT NULL,

                model_version TEXT
                NOT NULL,

                status TEXT
                NOT NULL,

                exit_candle_time TEXT,

                exit_price REAL,

                exit_reason TEXT,

                gross_pnl_pips REAL,

                net_pnl_pips REAL,

                r_multiple REAL,

                created_at TEXT
                NOT NULL,

                closed_at TEXT
            )
            """
        )

        connection.commit()


def get_previous_analysis(
    symbol,
    candle_time,
):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                signal,
                status

            FROM research_15m_analysis

            WHERE
                symbol = ?
                AND interval = ?
                AND candle_time < ?

            ORDER BY
                candle_time DESC

            LIMIT 1
            """,
            (
                symbol,
                RESEARCH_INTERVAL,
                candle_time,
            ),
        ).fetchone()

    if row is None:
        return None

    return dict(
        row
    )


def create_15m_trade_if_new(
    symbol,
    result,
):
    signal = result[
        "signal"
    ]

    status = result[
        "status"
    ]

    if (
        status != "VALID"
        or signal not in (
            "BUY",
            "SELL",
        )
    ):
        return None

    candle_time = result[
        "datetime"
    ]

    previous = get_previous_analysis(
        symbol,
        candle_time,
    )

    if (
        previous
        and previous[
            "status"
        ] == "VALID"
        and previous[
            "signal"
        ] == signal
    ):
        print(
            "15M RESEARCH | "
            f"{symbol} | "
            f"{signal} setup continues | "
            "no new trade",
            flush=True,
        )

        return None

    instrument = (
        get_instrument_config(
            symbol
        )
    )

    pip_size = instrument[
        "pip_size"
    ]

    min_stop_pips = instrument[
        "min_stop_pips"
    ]

    spread_pips = instrument[
        "assumed_spread_pips"
    ]

    entry_price = float(
        result[
            "close"
        ]
    )

    atr = float(
        result[
            "atr"
        ]
    )

    stop_distance = max(
        (
            atr
            * STOP_LOSS_ATR_MULTIPLIER
        ),
        (
            min_stop_pips
            * pip_size
        ),
    )

    target_distance = (
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
            + target_distance
        )

    else:
        stop_loss = (
            entry_price
            + stop_distance
        )

        take_profit = (
            entry_price
            - target_distance
        )

    risk_pips = (
        stop_distance
        / pip_size
    )

    reward_pips = (
        target_distance
        / pip_size
    )

    created_at = (
        datetime.now(
            timezone.utc
        ).strftime(
            TIME_FORMAT
        )
    )

    with get_connection() as connection:
        signal_cursor = (
            connection.execute(
                """
                INSERT OR IGNORE INTO
                research_15m_signals (
                    symbol,
                    interval,
                    candle_time,
                    signal,
                    entry_price,
                    atr,
                    setup_score,
                    created_at
                )

                VALUES (
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
                    candle_time,
                    signal,
                    entry_price,
                    atr,
                    int(
                        result[
                            "setup_score"
                        ]
                    ),
                    created_at,
                ),
            )
        )

        if (
            signal_cursor.rowcount
            == 0
        ):
            return None

        signal_id = (
            signal_cursor.lastrowid
        )

        trade_cursor = (
            connection.execute(
                """
                INSERT INTO
                research_15m_trades (
                    signal_id,
                    symbol,
                    interval,
                    signal,
                    entry_candle_time,
                    entry_price,
                    stop_loss,
                    take_profit,
                    risk_pips,
                    reward_pips,
                    spread_pips,
                    max_hold_minutes,
                    model_version,
                    status,
                    created_at
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
                    'OPEN',
                    ?
                )
                """,
                (
                    signal_id,
                    symbol,
                    RESEARCH_INTERVAL,
                    signal,
                    candle_time,
                    entry_price,
                    stop_loss,
                    take_profit,
                    risk_pips,
                    reward_pips,
                    spread_pips,
                    MAX_TRADE_MINUTES,
                    RESEARCH_MODEL_VERSION,
                    created_at,
                ),
            )
        )

        trade_id = (
            trade_cursor.lastrowid
        )

        connection.commit()

    return {
        "id":
            trade_id,

        "symbol":
            symbol,

        "signal":
            signal,

        "entry":
            entry_price,

        "stop_loss":
            stop_loss,

        "take_profit":
            take_profit,

        "risk_pips":
            risk_pips,

        "reward_pips":
            reward_pips,
    }


def get_open_15m_trades(
    symbol,
):
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *

            FROM research_15m_trades

            WHERE
                symbol = ?
                AND status = 'OPEN'

            ORDER BY
                id ASC
            """,
            (
                symbol,
            ),
        ).fetchall()

    return [
        dict(
            row
        )
        for row in rows
    ]


def get_future_15m_candles(
    symbol,
    start_time,
):
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                candle_time,
                high,
                low,
                close

            FROM research_15m_candles

            WHERE
                symbol = ?
                AND candle_time >= ?

            ORDER BY
                candle_time ASC
            """,
            (
                symbol,
                start_time,
            ),
        ).fetchall()

    return [
        dict(
            row
        )
        for row in rows
    ]


def close_15m_trade(
    trade_id,
    status,
    exit_candle_time,
    exit_price,
    exit_reason,
    gross_pips,
    net_pips,
    r_multiple,
):
    closed_at = (
        datetime.now(
            timezone.utc
        ).strftime(
            TIME_FORMAT
        )
    )

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE research_15m_trades

            SET
                status = ?,
                exit_candle_time = ?,
                exit_price = ?,
                exit_reason = ?,
                gross_pnl_pips = ?,
                net_pnl_pips = ?,
                r_multiple = ?,
                closed_at = ?

            WHERE
                id = ?
                AND status = 'OPEN'
            """,
            (
                status,
                exit_candle_time,
                exit_price,
                exit_reason,
                gross_pips,
                net_pips,
                r_multiple,
                closed_at,
                trade_id,
            ),
        )

        connection.commit()

    return (
        cursor.rowcount
        > 0
    )


def directional_pips(
    signal,
    entry_price,
    exit_price,
    pip_size,
):
    if signal == "BUY":
        difference = (
            exit_price
            - entry_price
        )

    else:
        difference = (
            entry_price
            - exit_price
        )

    return (
        difference
        / pip_size
    )


def evaluate_15m_open_trades(
    symbol,
):
    instrument = (
        get_instrument_config(
            symbol
        )
    )

    pip_size = instrument[
        "pip_size"
    ]

    results = []

    for trade in (
        get_open_15m_trades(
            symbol
        )
    ):
        entry_open = (
            datetime.strptime(
                trade[
                    "entry_candle_time"
                ],
                TIME_FORMAT,
            )
        )

        actual_entry_time = (
            entry_open
            + timedelta(
                minutes=(
                    RESEARCH_INTERVAL_MINUTES
                )
            )
        )

        maximum_exit_time = (
            actual_entry_time
            + timedelta(
                minutes=int(
                    trade[
                        "max_hold_minutes"
                    ]
                )
            )
        )

        candles = (
            get_future_15m_candles(
                symbol,
                actual_entry_time.strftime(
                    TIME_FORMAT
                ),
            )
        )

        signal = trade[
            "signal"
        ]

        entry_price = float(
            trade[
                "entry_price"
            ]
        )

        stop_loss = float(
            trade[
                "stop_loss"
            ]
        )

        take_profit = float(
            trade[
                "take_profit"
            ]
        )

        risk_pips = float(
            trade[
                "risk_pips"
            ]
        )

        reward_pips = float(
            trade[
                "reward_pips"
            ]
        )

        spread_pips = float(
            trade[
                "spread_pips"
            ]
        )

        for candle in candles:
            candle_open = (
                datetime.strptime(
                    candle[
                        "candle_time"
                    ],
                    TIME_FORMAT,
                )
            )

            candle_close = (
                candle_open
                + timedelta(
                    minutes=(
                        RESEARCH_INTERVAL_MINUTES
                    )
                )
            )

            high = float(
                candle[
                    "high"
                ]
            )

            low = float(
                candle[
                    "low"
                ]
            )

            close = float(
                candle[
                    "close"
                ]
            )

            if signal == "BUY":
                stop_hit = (
                    low
                    <= stop_loss
                )

                target_hit = (
                    high
                    >= take_profit
                )

            else:
                stop_hit = (
                    high
                    >= stop_loss
                )

                target_hit = (
                    low
                    <= take_profit
                )

            if (
                stop_hit
                and target_hit
            ):
                closed = close_15m_trade(
                    trade_id=(
                        trade[
                            "id"
                        ]
                    ),

                    status=(
                        "AMBIGUOUS"
                    ),

                    exit_candle_time=(
                        candle[
                            "candle_time"
                        ]
                    ),

                    exit_price=None,

                    exit_reason=(
                        "SL_AND_TP_"
                        "SAME_CANDLE"
                    ),

                    gross_pips=None,
                    net_pips=None,
                    r_multiple=None,
                )

                if closed:
                    results.append(
                        {
                            "trade_id":
                                trade[
                                    "id"
                                ],

                            "symbol":
                                symbol,

                            "signal":
                                signal,

                            "result":
                                "AMBIGUOUS",

                            "candle_time":
                                candle[
                                    "candle_time"
                                ],

                            "net_pips":
                                None,

                            "r":
                                None,
                        }
                    )

                break

            if target_hit:
                gross_pips = (
                    reward_pips
                )

                net_pips = (
                    gross_pips
                    - spread_pips
                )

                r_multiple = (
                    net_pips
                    / risk_pips
                )

                closed = close_15m_trade(
                    trade_id=(
                        trade[
                            "id"
                        ]
                    ),

                    status=(
                        "CLOSED"
                    ),

                    exit_candle_time=(
                        candle[
                            "candle_time"
                        ]
                    ),

                    exit_price=(
                        take_profit
                    ),

                    exit_reason=(
                        "TAKE_PROFIT"
                    ),

                    gross_pips=(
                        gross_pips
                    ),

                    net_pips=(
                        net_pips
                    ),

                    r_multiple=(
                        r_multiple
                    ),
                )

                if closed:
                    results.append(
                        {
                            "trade_id":
                                trade[
                                    "id"
                                ],

                            "symbol":
                                symbol,

                            "signal":
                                signal,

                            "result":
                                "TAKE_PROFIT",

                            "candle_time":
                                candle[
                                    "candle_time"
                                ],

                            "net_pips":
                                net_pips,

                            "r":
                                r_multiple,
                        }
                    )

                break

            if stop_hit:
                gross_pips = (
                    -risk_pips
                )

                net_pips = (
                    gross_pips
                    - spread_pips
                )

                r_multiple = (
                    net_pips
                    / risk_pips
                )

                closed = close_15m_trade(
                    trade_id=(
                        trade[
                            "id"
                        ]
                    ),

                    status=(
                        "CLOSED"
                    ),

                    exit_candle_time=(
                        candle[
                            "candle_time"
                        ]
                    ),

                    exit_price=(
                        stop_loss
                    ),

                    exit_reason=(
                        "STOP_LOSS"
                    ),

                    gross_pips=(
                        gross_pips
                    ),

                    net_pips=(
                        net_pips
                    ),

                    r_multiple=(
                        r_multiple
                    ),
                )

                if closed:
                    results.append(
                        {
                            "trade_id":
                                trade[
                                    "id"
                                ],

                            "symbol":
                                symbol,

                            "signal":
                                signal,

                            "result":
                                "STOP_LOSS",

                            "candle_time":
                                candle[
                                    "candle_time"
                                ],

                            "net_pips":
                                net_pips,

                            "r":
                                r_multiple,
                        }
                    )

                break

            if (
                candle_close
                >= maximum_exit_time
            ):
                gross_pips = (
                    directional_pips(
                        signal=signal,
                        entry_price=(
                            entry_price
                        ),
                        exit_price=(
                            close
                        ),
                        pip_size=(
                            pip_size
                        ),
                    )
                )

                net_pips = (
                    gross_pips
                    - spread_pips
                )

                r_multiple = (
                    net_pips
                    / risk_pips
                )

                closed = close_15m_trade(
                    trade_id=(
                        trade[
                            "id"
                        ]
                    ),

                    status=(
                        "CLOSED"
                    ),

                    exit_candle_time=(
                        candle[
                            "candle_time"
                        ]
                    ),

                    exit_price=(
                        close
                    ),

                    exit_reason=(
                        "TIMEOUT"
                    ),

                    gross_pips=(
                        gross_pips
                    ),

                    net_pips=(
                        net_pips
                    ),

                    r_multiple=(
                        r_multiple
                    ),
                )

                if closed:
                    results.append(
                        {
                            "trade_id":
                                trade[
                                    "id"
                                ],

                            "symbol":
                                symbol,

                            "signal":
                                signal,

                            "result":
                                "TIMEOUT",

                            "candle_time":
                                candle[
                                    "candle_time"
                                ],

                            "net_pips":
                                net_pips,

                            "r":
                                r_multiple,
                        }
                    )

                break

    return results


def get_15m_trade_summary(
    symbol,
):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total,

                SUM(
                    CASE
                    WHEN status = 'OPEN'
                    THEN 1
                    ELSE 0
                    END
                ) AS open_trades,

                SUM(
                    CASE
                    WHEN exit_reason = 'TAKE_PROFIT'
                    THEN 1
                    ELSE 0
                    END
                ) AS take_profits,

                SUM(
                    CASE
                    WHEN exit_reason = 'STOP_LOSS'
                    THEN 1
                    ELSE 0
                    END
                ) AS stop_losses,

                SUM(
                    CASE
                    WHEN exit_reason = 'TIMEOUT'
                    THEN 1
                    ELSE 0
                    END
                ) AS timeouts,

                SUM(
                    CASE
                    WHEN status = 'AMBIGUOUS'
                    THEN 1
                    ELSE 0
                    END
                ) AS ambiguous,

                SUM(
                    COALESCE(
                        net_pnl_pips,
                        0
                    )
                ) AS total_net_pips,

                AVG(
                    net_pnl_pips
                ) AS avg_net_pips,

                AVG(
                    r_multiple
                ) AS avg_r

            FROM research_15m_trades

            WHERE symbol = ?
            """,
            (
                symbol,
            ),
        ).fetchone()

    return dict(
        row
    )


def print_15m_trade_summary(
    symbol,
):
    summary = (
        get_15m_trade_summary(
            symbol
        )
    )

    total = int(
        summary[
            "total"
        ]
        or 0
    )

    if total == 0:
        return

    print(
        f"----- {symbol} "
        "15M TRADE RESEARCH -----",
        flush=True,
    )

    print(
        f"Trades="
        f"{total} | "

        f"TP="
        f"{summary['take_profits'] or 0} | "

        f"SL="
        f"{summary['stop_losses'] or 0} | "

        f"Timeout="
        f"{summary['timeouts'] or 0} | "

        f"Ambiguous="
        f"{summary['ambiguous'] or 0} | "

        f"Open="
        f"{summary['open_trades'] or 0} | "

        f"NetPips="
        f"{summary['total_net_pips'] or 0:+.2f} | "

        f"AvgNet="
        f"{summary['avg_net_pips'] or 0:+.2f} | "

        f"AvgR="
        f"{summary['avg_r'] or 0:+.2f}R",
        flush=True,
    )


def process_15m_trade_research(
    symbol,
    analysis_result,
):
    init_15m_trade_tables()

    new_trade = (
        create_15m_trade_if_new(
            symbol,
            analysis_result,
        )
    )

    if new_trade:
        print(
            "15M RESEARCH TRADE OPENED | "
            f"{symbol} | "
            f"ID="
            f"{new_trade['id']} | "
            f"{new_trade['signal']} | "
            f"Entry="
            f"{new_trade['entry']:.5f} | "
            f"SL="
            f"{new_trade['stop_loss']:.5f} | "
            f"TP="
            f"{new_trade['take_profit']:.5f} | "
            f"Risk="
            f"{new_trade['risk_pips']:.2f} pips | "
            f"Reward="
            f"{new_trade['reward_pips']:.2f} pips",
            flush=True,
        )

    results = (
        evaluate_15m_open_trades(
            symbol
        )
    )

    for result in results:
        if (
            result[
                "result"
            ]
            == "AMBIGUOUS"
        ):
            print(
                "15M RESEARCH TRADE RESULT | "
                f"{symbol} | "
                f"ID="
                f"{result['trade_id']} | "
                "AMBIGUOUS | "
                f"Candle="
                f"{result['candle_time']}",
                flush=True,
            )

        else:
            print(
                "15M RESEARCH TRADE CLOSED | "
                f"{symbol} | "
                f"ID="
                f"{result['trade_id']} | "
                f"{result['signal']} | "
                f"{result['result']} | "
                f"Net="
                f"{result['net_pips']:+.2f} pips | "
                f"R="
                f"{result['r']:+.2f}R | "
                f"Candle="
                f"{result['candle_time']}",
                flush=True,
            )

    if (
        new_trade
        or results
    ):
        print_15m_trade_summary(
            symbol
        )

    return {
        "new_trade":
            new_trade,

        "closed":
            results,
    }
