import sqlite3

from datetime import (
    datetime,
    timedelta,
)

from pathlib import Path


DB_PATH = Path(
    "/app/data/trading.db"
)


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_connection():

    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        DB_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    return connection


def get_columns(connection):

    rows = connection.execute(
        """
        PRAGMA table_info(
            market_analysis
        )
        """
    ).fetchall()

    return {
        row["name"]
        for row in rows
    }


def interval_minutes(interval):

    if interval.endswith("min"):

        return int(
            interval.replace(
                "min",
                "",
            )
        )

    if interval.endswith("h"):

        return (
            int(
                interval.replace(
                    "h",
                    "",
                )
            )
            * 60
        )

    raise ValueError(
        f"Unsupported interval: "
        f"{interval}"
    )


def init_db():

    with get_connection() as connection:

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            market_analysis (

                id INTEGER
                PRIMARY KEY AUTOINCREMENT,

                created_at TEXT NOT NULL,
                candle_time TEXT NOT NULL,

                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,

                close REAL NOT NULL,

                ema_fast REAL NOT NULL,
                ema_slow REAL NOT NULL,

                rsi REAL NOT NULL,
                atr REAL NOT NULL,

                ema_distance_atr REAL NOT NULL,

                trend TEXT NOT NULL,
                signal TEXT NOT NULL,

                confidence INTEGER NOT NULL,
                reason TEXT
            )
            """
        )

        columns = get_columns(
            connection
        )

        migrations = {

            "ema_direction":
                "TEXT",

            "setup_score":
                "INTEGER NOT NULL DEFAULT 0",

            "status":
                "TEXT NOT NULL DEFAULT 'BLOCKED'",

            "blockers":
                "TEXT",
        }

        for column, definition in (
            migrations.items()
        ):

            if column not in columns:

                connection.execute(
                    f"""
                    ALTER TABLE
                    market_analysis

                    ADD COLUMN
                    {column}
                    {definition}
                    """
                )

        connection.execute(
            """
            DELETE FROM market_analysis

            WHERE id NOT IN (

                SELECT MIN(id)

                FROM market_analysis

                GROUP BY
                    symbol,
                    interval,
                    candle_time
            )
            """
        )

        connection.execute(
            """
            CREATE UNIQUE INDEX
            IF NOT EXISTS
            idx_unique_market_candle

            ON market_analysis (
                symbol,
                interval,
                candle_time
            )
            """
        )

        # =========================
        # SIGNAL EVENTS
        # =========================

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            signal_events (

                id INTEGER
                PRIMARY KEY AUTOINCREMENT,

                analysis_id INTEGER
                NOT NULL UNIQUE,

                created_at TEXT NOT NULL,
                candle_time TEXT NOT NULL,

                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,

                signal TEXT NOT NULL,

                entry_price REAL NOT NULL,

                setup_score INTEGER NOT NULL
            )
            """
        )

        # =========================
        # OLD 15/30/60 OUTCOMES
        # =========================

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            signal_event_outcomes (

                id INTEGER
                PRIMARY KEY AUTOINCREMENT,

                signal_event_id INTEGER
                NOT NULL,

                horizon_minutes INTEGER
                NOT NULL,

                target_candle_time TEXT
                NOT NULL,

                target_close REAL
                NOT NULL,

                directional_pips REAL
                NOT NULL,

                result TEXT NOT NULL,

                evaluated_at TEXT
                NOT NULL,

                UNIQUE (
                    signal_event_id,
                    horizon_minutes
                )
            )
            """
        )

        # =========================
        # VIRTUAL TRADES
        # =========================

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            virtual_trades (

                id INTEGER
                PRIMARY KEY AUTOINCREMENT,

                signal_event_id INTEGER
                NOT NULL UNIQUE,

                created_at TEXT NOT NULL,

                entry_candle_time TEXT
                NOT NULL,

                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,

                signal TEXT NOT NULL,

                entry_price REAL NOT NULL,
                atr REAL NOT NULL,

                stop_loss REAL NOT NULL,
                take_profit REAL NOT NULL,

                risk_pips REAL NOT NULL,
                reward_pips REAL NOT NULL,

                status TEXT NOT NULL
                DEFAULT 'OPEN',

                exit_candle_time TEXT,

                exit_price REAL,

                exit_reason TEXT,

                pnl_pips REAL
            )
            """
        )

        connection.commit()


def save_analysis(
    created_at,
    symbol,
    interval,
    result,
):

    blockers = "; ".join(
        result.get(
            "blockers",
            [],
        )
    )

    with get_connection() as connection:

        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO
            market_analysis (

                created_at,
                candle_time,

                symbol,
                interval,

                close,

                ema_fast,
                ema_slow,

                rsi,
                atr,

                ema_distance_atr,

                trend,
                signal,

                confidence,
                reason,

                ema_direction,
                setup_score,
                status,
                blockers
            )

            VALUES (
                ?, ?,
                ?, ?,
                ?,
                ?, ?,
                ?, ?,
                ?,
                ?, ?,
                ?, ?,
                ?, ?, ?, ?
            )
            """,
            (

                created_at,
                result["datetime"],

                symbol,
                interval,

                result["close"],

                result["ema_fast"],
                result["ema_slow"],

                result["rsi"],
                result["atr"],

                result[
                    "ema_distance_atr"
                ],

                result["trend"],
                result["signal"],

                result["setup_score"],
                result["reason"],

                result["ema_direction"],
                result["setup_score"],
                result["status"],
                blockers,
            ),
        )

        inserted = (
            cursor.rowcount > 0
        )

        row = connection.execute(
            """
            SELECT id

            FROM market_analysis

            WHERE symbol = ?
            AND interval = ?
            AND candle_time = ?
            """,
            (
                symbol,
                interval,
                result["datetime"],
            ),
        ).fetchone()

        connection.commit()

        return (
            inserted,
            row["id"]
            if row
            else None,
        )


def create_signal_event_if_new(
    analysis_id,
    created_at,
    symbol,
    interval,
    result,
):

    if (
        result["status"] != "VALID"
        or result["signal"]
        not in (
            "BUY",
            "SELL",
        )
    ):

        return (
            False,
            None,
            "NO_SIGNAL",
        )

    with get_connection() as connection:

        existing = connection.execute(
            """
            SELECT id

            FROM signal_events

            WHERE analysis_id = ?
            """,
            (
                analysis_id,
            ),
        ).fetchone()

        if existing:

            return (
                False,
                existing["id"],
                "ALREADY_EXISTS",
            )

        last_event = connection.execute(
            """
            SELECT
                id,
                candle_time,
                signal

            FROM signal_events

            WHERE symbol = ?
            AND interval = ?

            ORDER BY candle_time DESC

            LIMIT 1
            """,
            (
                symbol,
                interval,
            ),
        ).fetchone()

        if last_event is None:

            continuation = False

        else:

            previous = connection.execute(
                """
                SELECT
                    candle_time,
                    signal,
                    status

                FROM market_analysis

                WHERE symbol = ?
                AND interval = ?
                AND candle_time < ?

                ORDER BY candle_time DESC

                LIMIT 1
                """,
                (
                    symbol,
                    interval,
                    result["datetime"],
                ),
            ).fetchone()

            continuation = False

            if previous:

                current_time = (
                    datetime.strptime(
                        result["datetime"],
                        TIME_FORMAT,
                    )
                )

                previous_time = (
                    datetime.strptime(
                        previous[
                            "candle_time"
                        ],
                        TIME_FORMAT,
                    )
                )

                expected_gap = timedelta(
                    minutes=interval_minutes(
                        interval
                    )
                )

                actual_gap = (
                    current_time
                    - previous_time
                )

                if (
                    actual_gap
                    == expected_gap

                    and previous["status"]
                    == "VALID"

                    and previous["signal"]
                    == result["signal"]
                ):

                    continuation = True

        if continuation:

            return (
                False,
                None,
                "CONTINUATION",
            )

        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO
            signal_events (

                analysis_id,

                created_at,
                candle_time,

                symbol,
                interval,

                signal,

                entry_price,
                setup_score
            )

            VALUES (
                ?, ?,
                ?, ?,
                ?,
                ?, ?, ?
            )
            """,
            (
                analysis_id,

                created_at,
                result["datetime"],

                symbol,
                interval,

                result["signal"],

                result["close"],
                result["setup_score"],
            ),
        )

        connection.commit()

        if cursor.rowcount == 0:

            return (
                False,
                None,
                "ALREADY_EXISTS",
            )

        return (
            True,
            cursor.lastrowid,
            "NEW_SIGNAL",
        )


def count_records():

    with get_connection() as connection:

        row = connection.execute(
            """
            SELECT COUNT(*) AS total

            FROM market_analysis
            """
        ).fetchone()

        return row["total"]


def count_signal_events():

    with get_connection() as connection:

        row = connection.execute(
            """
            SELECT COUNT(*) AS total

            FROM signal_events
            """
        ).fetchone()

        return row["total"]


# =========================
# SIGNAL OUTCOMES
# =========================

def get_pending_signal_events():

    with get_connection() as connection:

        rows = connection.execute(
            """
            SELECT

                id AS signal_event_id,

                candle_time,

                entry_price,

                signal,
                setup_score

            FROM signal_events

            ORDER BY candle_time ASC
            """
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]


def outcome_exists(
    signal_event_id,
    horizon_minutes,
):

    with get_connection() as connection:

        row = connection.execute(
            """
            SELECT id

            FROM signal_event_outcomes

            WHERE signal_event_id = ?
            AND horizon_minutes = ?
            """,
            (
                signal_event_id,
                horizon_minutes,
            ),
        ).fetchone()

        return row is not None


def save_signal_event_outcome(
    signal_event_id,
    horizon_minutes,

    target_candle_time,
    target_close,

    directional_pips,
    result,

    evaluated_at,
):

    with get_connection() as connection:

        connection.execute(
            """
            INSERT OR IGNORE INTO
            signal_event_outcomes (

                signal_event_id,
                horizon_minutes,

                target_candle_time,
                target_close,

                directional_pips,
                result,

                evaluated_at
            )

            VALUES (
                ?, ?,
                ?, ?,
                ?, ?,
                ?
            )
            """,
            (
                signal_event_id,
                horizon_minutes,

                target_candle_time,
                target_close,

                directional_pips,
                result,

                evaluated_at,
            ),
        )

        connection.commit()


def get_outcome_summary():

    with get_connection() as connection:

        rows = connection.execute(
            """
            SELECT

                horizon_minutes,

                COUNT(*) AS total,

                SUM(
                    CASE
                    WHEN result = 'WIN'
                    THEN 1
                    ELSE 0
                    END
                ) AS wins,

                SUM(
                    CASE
                    WHEN result = 'LOSS'
                    THEN 1
                    ELSE 0
                    END
                ) AS losses,

                SUM(
                    CASE
                    WHEN result = 'FLAT'
                    THEN 1
                    ELSE 0
                    END
                ) AS flat,

                AVG(
                    directional_pips
                ) AS avg_pips

            FROM signal_event_outcomes

            GROUP BY horizon_minutes

            ORDER BY horizon_minutes
            """
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]


# =========================
# VIRTUAL TRADE STORAGE
# =========================

def get_signal_events_without_trades():

    with get_connection() as connection:

        rows = connection.execute(
            """
            SELECT

                se.id
                AS signal_event_id,

                se.created_at,

                se.candle_time,

                se.symbol,
                se.interval,

                se.signal,

                se.entry_price,

                se.setup_score,

                ma.atr

            FROM signal_events se

            JOIN market_analysis ma
            ON ma.id = se.analysis_id

            LEFT JOIN virtual_trades vt
            ON vt.signal_event_id = se.id

            WHERE vt.id IS NULL

            ORDER BY se.candle_time ASC
            """
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]


def save_virtual_trade(
    signal_event_id,
    created_at,

    entry_candle_time,

    symbol,
    interval,

    signal,

    entry_price,
    atr,

    stop_loss,
    take_profit,

    risk_pips,
    reward_pips,
):

    with get_connection() as connection:

        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO
            virtual_trades (

                signal_event_id,

                created_at,

                entry_candle_time,

                symbol,
                interval,

                signal,

                entry_price,
                atr,

                stop_loss,
                take_profit,

                risk_pips,
                reward_pips,

                status
            )

            VALUES (
                ?,
                ?,
                ?,
                ?, ?,
                ?,
                ?, ?,
                ?, ?,
                ?, ?,
                'OPEN'
            )
            """,
            (
                signal_event_id,

                created_at,

                entry_candle_time,

                symbol,
                interval,

                signal,

                entry_price,
                atr,

                stop_loss,
                take_profit,

                risk_pips,
                reward_pips,
            ),
        )

        connection.commit()

        if cursor.rowcount == 0:

            return (
                False,
                None,
            )

        return (
            True,
            cursor.lastrowid,
        )


def get_open_virtual_trades():

    with get_connection() as connection:

        rows = connection.execute(
            """
            SELECT *

            FROM virtual_trades

            WHERE status = 'OPEN'

            ORDER BY entry_candle_time ASC
            """
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]


def close_virtual_trade(
    trade_id,

    status,

    exit_candle_time,

    exit_price,

    exit_reason,

    pnl_pips,
):

    with get_connection() as connection:

        cursor = connection.execute(
            """
            UPDATE virtual_trades

            SET
                status = ?,

                exit_candle_time = ?,

                exit_price = ?,

                exit_reason = ?,

                pnl_pips = ?

            WHERE id = ?
            AND status = 'OPEN'
            """,
            (
                status,

                exit_candle_time,

                exit_price,

                exit_reason,

                pnl_pips,

                trade_id,
            ),
        )

        connection.commit()

        return cursor.rowcount > 0


def count_virtual_trades():

    with get_connection() as connection:

        row = connection.execute(
            """
            SELECT COUNT(*) AS total

            FROM virtual_trades
            """
        ).fetchone()

        return row["total"]


def count_open_virtual_trades():

    with get_connection() as connection:

        row = connection.execute(
            """
            SELECT COUNT(*) AS total

            FROM virtual_trades

            WHERE status = 'OPEN'
            """
        ).fetchone()

        return row["total"]


def get_trade_summary():

    with get_connection() as connection:

        row = connection.execute(
            """
            SELECT

                COUNT(*) AS total,

                SUM(
                    CASE
                    WHEN exit_reason =
                    'TAKE_PROFIT'
                    THEN 1
                    ELSE 0
                    END
                ) AS take_profits,

                SUM(
                    CASE
                    WHEN exit_reason =
                    'STOP_LOSS'
                    THEN 1
                    ELSE 0
                    END
                ) AS stop_losses,

                SUM(
                    CASE
                    WHEN status =
                    'AMBIGUOUS'
                    THEN 1
                    ELSE 0
                    END
                ) AS ambiguous,

                SUM(
                    CASE
                    WHEN status =
                    'OPEN'
                    THEN 1
                    ELSE 0
                    END
                ) AS open_trades,

                AVG(
                    CASE
                    WHEN status = 'CLOSED'
                    THEN pnl_pips
                    ELSE NULL
                    END
                ) AS avg_pips,

                SUM(
                    CASE
                    WHEN status = 'CLOSED'
                    THEN pnl_pips
                    ELSE 0
                    END
                ) AS total_pips

            FROM virtual_trades
            """
        ).fetchone()

        return dict(row)
