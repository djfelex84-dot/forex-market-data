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
            "ema_direction":
                "TEXT",

            "setup_score":
                "INTEGER NOT NULL DEFAULT 0",

            "status":
                "TEXT NOT NULL DEFAULT 'BLOCKED'",

            "blockers":
                "TEXT",
        }

        for column, definition in migrations.items():
            if column not in columns:
                connection.execute(
                    f"""
                    ALTER TABLE market_analysis
                    ADD COLUMN {column} {definition}
                    """
                )

        # Если раньше случайно сохранились одинаковые свечи,
        # оставляем только одну.
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

        # Одна свеча конкретной пары и таймфрейма
        # может находиться в базе только один раз.
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

        connection.commit()

        return cursor.rowcount > 0


def count_records():
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM market_analysis
            """
        ).fetchone()

        return row["total"]
