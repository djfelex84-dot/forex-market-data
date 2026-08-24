import sqlite3
from pathlib import Path


DB_PATH = Path("/app/data/trading.db")


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    return connection


def get_columns(connection):
    rows = connection.execute(
        "PRAGMA table_info(market_analysis)"
    ).fetchall()

    return {row["name"] for row in rows}


def init_db():
    with get_connection() as connection:

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS market_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

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

        columns = get_columns(connection)

        migrations = {
            "ema_direction": "TEXT",
            "setup_score": "INTEGER NOT NULL DEFAULT 0",
            "status": "TEXT NOT NULL DEFAULT 'BLOCKED'",
            "blockers": "TEXT",
        }

        for column, definition in migrations.items():
            if column not in columns:
                connection.execute(
                    f"""
                    ALTER TABLE market_analysis
                    ADD COLUMN {column} {definition}
                    """
                )

        # Удаляем возможные старые дубликаты свечей
        connection.execute(
            """
            DELETE FROM market_analysis
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM market_analysis
                GROUP BY symbol, interval, candle_time
            )
            """
        )

        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_unique_market_candle
            ON market_analysis (
                symbol,
                interval,
                candle_time
            )
            """
        )

        # Результаты BUY / SELL
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                analysis_id INTEGER NOT NULL,
                horizon_minutes INTEGER NOT NULL,

                target_candle_time TEXT NOT NULL,
                target_close REAL NOT NULL,

                directional_pips REAL NOT NULL,
                result TEXT NOT NULL,

                evaluated_at TEXT NOT NULL,

                UNIQUE (
                    analysis_id,
                    horizon_minutes
                )
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_signal_outcomes_analysis
            ON signal_outcomes (analysis_id)
            """
        )

        connection.commit()


def save_analysis(
    created_at,
    symbol,
    interval,
    result,
):
    blockers = "; ".join(result["blockers"])

    with get_connection() as connection:

        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO market_analysis (
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
                ?, ?, ?, ?,
                ?, ?, ?,
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

                result["ema_distance_atr"],

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

        inserted = cursor.rowcount > 0

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

        analysis_id = row["id"] if row else None

        return inserted, analysis_id


def count_records():
    with get_connection() as connection:

        row = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM market_analysis
            """
        ).fetchone()

        return row["total"]


def get_pending_signals():
    with get_connection() as connection:

        rows = connection.execute(
            """
            SELECT
                id,
                candle_time,
                close,
                signal,
                setup_score

            FROM market_analysis

            WHERE signal IN ('BUY', 'SELL')
              AND status = 'VALID'

            ORDER BY candle_time ASC
            """
        ).fetchall()

        return [dict(row) for row in rows]


def outcome_exists(
    analysis_id,
    horizon_minutes,
):
    with get_connection() as connection:

        row = connection.execute(
            """
            SELECT id
            FROM signal_outcomes

            WHERE analysis_id = ?
              AND horizon_minutes = ?
            """,
            (
                analysis_id,
                horizon_minutes,
            ),
        ).fetchone()

        return row is not None


def save_signal_outcome(
    analysis_id,
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
            INSERT OR IGNORE INTO signal_outcomes (
                analysis_id,
                horizon_minutes,

                target_candle_time,
                target_close,

                directional_pips,
                result,

                evaluated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                analysis_id,
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

                AVG(directional_pips) AS avg_pips

            FROM signal_outcomes

            GROUP BY horizon_minutes
            ORDER BY horizon_minutes
            """
        ).fetchall()

        return [dict(row) for row in rows]
