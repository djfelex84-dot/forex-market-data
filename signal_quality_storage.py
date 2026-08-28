import os
import sqlite3

from datetime import (
    datetime,
    timezone,
)

from math import isfinite
from pathlib import Path


QUALITY_DB_PATH = Path(
    os.getenv(
        "SIGNAL_QUALITY_DB_PATH",
        "/app/data/signal_quality.db",
    )
)

SNAPSHOT_VERSION = "SQ1"

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


NUMERIC_FIELDS = (
    "strategy_score",
    "rsi",

    "atr_fast",
    "atr_slow",
    "atr_regime",

    "ema_separation_atr",
    "ema_fast_slope_atr",
    "ema_slow_slope_atr",

    "price_extension_atr",

    "candle_range_atr",
    "candle_body_atr",
    "directional_body_atr",
    "directional_change_atr",
    "directional_close_location",

    "previous_close_break_atr",

    "momentum_3_atr",
    "momentum_6_atr",

    "nearest_ema20_distance_atr",
    "min_directional_ema20_distance_atr",
    "deepest_ema20_break_atr",
)


def _get_connection():
    QUALITY_DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        QUALITY_DB_PATH,
        timeout=10,
    )

    connection.row_factory = (
        sqlite3.Row
    )

    return connection


def _optional_float(
    value,
):
    if value is None:
        return None

    try:
        result = float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        return None

    if not isfinite(
        result
    ):
        return None

    return result


def init_quality_storage():
    with _get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            quality_snapshots (

                id INTEGER
                PRIMARY KEY AUTOINCREMENT,

                created_at TEXT NOT NULL,
                candle_time TEXT NOT NULL,

                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,

                snapshot_version TEXT NOT NULL,

                analysis_id INTEGER,

                direction TEXT NOT NULL,
                strategy_status TEXT,
                strategy_score REAL,

                rsi REAL,

                atr_fast REAL,
                atr_slow REAL,
                atr_regime REAL,

                ema_separation_atr REAL,
                ema_fast_slope_atr REAL,
                ema_slow_slope_atr REAL,

                price_extension_atr REAL,

                candle_range_atr REAL,
                candle_body_atr REAL,
                directional_body_atr REAL,
                directional_change_atr REAL,
                directional_close_location REAL,

                previous_close_break_atr REAL,

                momentum_3_atr REAL,
                momentum_6_atr REAL,

                nearest_ema20_distance_atr REAL,
                min_directional_ema20_distance_atr REAL,
                deepest_ema20_break_atr REAL
            )
            """
        )

        connection.execute(
            """
            CREATE UNIQUE INDEX
            IF NOT EXISTS
            idx_quality_snapshot_unique

            ON quality_snapshots (
                symbol,
                interval,
                candle_time,
                snapshot_version
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX
            IF NOT EXISTS
            idx_quality_snapshot_lookup

            ON quality_snapshots (
                snapshot_version,
                symbol,
                interval,
                candle_time
            )
            """
        )

        connection.commit()


def save_quality_snapshot(
    symbol,
    snapshot,
    analysis_id=None,
    created_at=None,
):
    if not symbol:
        raise ValueError(
            "symbol is required"
        )

    candle_time = snapshot.get(
        "datetime"
    )

    interval = snapshot.get(
        "interval"
    )

    direction = snapshot.get(
        "direction"
    )

    if not candle_time:
        raise ValueError(
            "snapshot datetime is required"
        )

    if not interval:
        raise ValueError(
            "snapshot interval is required"
        )

    if not direction:
        raise ValueError(
            "snapshot direction is required"
        )

    if created_at is None:
        created_at = (
            datetime.now(
                timezone.utc
            ).strftime(
                TIME_FORMAT
            )
        )

    numeric_values = {
        field:
            _optional_float(
                snapshot.get(
                    field
                )
            )
        for field in NUMERIC_FIELDS
    }

    with _get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO
            quality_snapshots (

                created_at,
                candle_time,

                symbol,
                interval,

                snapshot_version,
                analysis_id,

                direction,
                strategy_status,
                strategy_score,

                rsi,

                atr_fast,
                atr_slow,
                atr_regime,

                ema_separation_atr,
                ema_fast_slope_atr,
                ema_slow_slope_atr,

                price_extension_atr,

                candle_range_atr,
                candle_body_atr,
                directional_body_atr,
                directional_change_atr,
                directional_close_location,

                previous_close_break_atr,

                momentum_3_atr,
                momentum_6_atr,

                nearest_ema20_distance_atr,
                min_directional_ema20_distance_atr,
                deepest_ema20_break_atr
            )

            VALUES (
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?,
                ?,
                ?, ?, ?,
                ?, ?, ?,
                ?,
                ?, ?, ?, ?, ?,
                ?,
                ?, ?,
                ?, ?, ?
            )
            """,
            (
                created_at,
                candle_time,

                symbol,
                interval,

                SNAPSHOT_VERSION,
                analysis_id,

                direction,
                snapshot.get(
                    "strategy_status"
                ),
                numeric_values[
                    "strategy_score"
                ],

                numeric_values[
                    "rsi"
                ],

                numeric_values[
                    "atr_fast"
                ],
                numeric_values[
                    "atr_slow"
                ],
                numeric_values[
                    "atr_regime"
                ],

                numeric_values[
                    "ema_separation_atr"
                ],
                numeric_values[
                    "ema_fast_slope_atr"
                ],
                numeric_values[
                    "ema_slow_slope_atr"
                ],

                numeric_values[
                    "price_extension_atr"
                ],

                numeric_values[
                    "candle_range_atr"
                ],
                numeric_values[
                    "candle_body_atr"
                ],
                numeric_values[
                    "directional_body_atr"
                ],
                numeric_values[
                    "directional_change_atr"
                ],
                numeric_values[
                    "directional_close_location"
                ],

                numeric_values[
                    "previous_close_break_atr"
                ],

                numeric_values[
                    "momentum_3_atr"
                ],
                numeric_values[
                    "momentum_6_atr"
                ],

                numeric_values[
                    "nearest_ema20_distance_atr"
                ],
                numeric_values[
                    "min_directional_ema20_distance_atr"
                ],
                numeric_values[
                    "deepest_ema20_break_atr"
                ],
            ),
        )

        connection.commit()

        return (
            cursor.rowcount > 0
        )


def count_quality_snapshots(
    symbol=None,
    interval=None,
):
    query = (
        "SELECT COUNT(*) AS total "
        "FROM quality_snapshots "
        "WHERE snapshot_version = ?"
    )

    params = [
        SNAPSHOT_VERSION
    ]

    if symbol is not None:
        query += (
            " AND symbol = ?"
        )

        params.append(
            symbol
        )

    if interval is not None:
        query += (
            " AND interval = ?"
        )

        params.append(
            interval
        )

    with _get_connection() as connection:
        row = connection.execute(
            query,
            params,
        ).fetchone()

        if row is None:
            return 0

        return int(
            row["total"]
        )
