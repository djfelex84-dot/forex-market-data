import sqlite3
from pathlib import Path

DB_PATH = Path("/app/data/trading.db")


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


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

        connection.commit()


def save_analysis(
    created_at,
    symbol,
    interval,
    result,
):
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO market_analysis (
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
                reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                result["confidence"],
                result["reason"],
            ),
        )

        connection.commit()


def count_records():
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM market_analysis"
        ).fetchone()

        return row["total"]
