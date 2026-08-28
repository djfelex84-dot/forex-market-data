import os
import sqlite3

from datetime import (
    datetime,
)
from math import (
    isfinite,
)


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

SIGNAL_INTERVAL = "30min"
SIGNAL_INTERVAL_MINUTES = 30

SHADOW_VERSION = "M30H1_SHADOW_V1"
LEGACY_STATE_SHADOW_VERSION = "M30H1_SHADOW_V1"

DEFAULT_DB_PATH = os.getenv(
    "MTF_SHADOW_DB_PATH",
    "/app/data/mtf_shadow_signals.db",
)

VALID_DIRECTIONS = {
    "BUY",
    "SELL",
}

VALID_ALIGNMENTS = {
    "ALIGNED",
    "CONFLICT",
    "UNKNOWN",
}


def _resolve_db_path(
    db_path=None,
):
    return (
        db_path
        or DEFAULT_DB_PATH
    )


def _ensure_parent_directory(
    db_path,
):
    parent = os.path.dirname(
        db_path
    )

    if parent:
        os.makedirs(
            parent,
            exist_ok=True,
        )


def _connect(
    db_path=None,
):
    path = _resolve_db_path(
        db_path
    )

    _ensure_parent_directory(
        path
    )

    connection = sqlite3.connect(
        path
    )

    connection.row_factory = (
        sqlite3.Row
    )

    return connection


def _normalize_direction(
    value,
):
    if value is None:
        return None

    direction = str(
        value
    ).upper()

    if direction in VALID_DIRECTIONS:
        return direction

    return None


def _normalize_alignment(
    value,
):
    alignment = str(
        value
        or "UNKNOWN"
    ).upper()

    if alignment in VALID_ALIGNMENTS:
        return alignment

    return "UNKNOWN"


def _optional_float(
    value,
):
    if value is None:
        return None

    try:
        number = float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        return None

    if not isfinite(
        number
    ):
        return None

    return number


def _optional_int(
    value,
):
    if value is None:
        return None

    try:
        return int(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


def _parse_time(
    value,
):
    return datetime.strptime(
        str(value),
        TIME_FORMAT,
    )


def _get_table_info(
    connection,
    table_name,
):
    return connection.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()


def _get_table_columns(
    connection,
    table_name,
):
    return {
        str(
            row["name"]
        )
        for row in _get_table_info(
            connection,
            table_name,
        )
    }


def _ensure_signal_atr_column(
    connection,
):
    columns = _get_table_columns(
        connection,
        "mtf_shadow_signals",
    )

    if (
        "signal_atr"
        not in columns
    ):
        connection.execute(
            """
            ALTER TABLE mtf_shadow_signals
            ADD COLUMN signal_atr REAL
            """
        )


def _create_versioned_state_table(
    connection,
):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS mtf_shadow_state (
            symbol TEXT NOT NULL,

            signal_interval TEXT NOT NULL,

            shadow_version TEXT NOT NULL,

            last_candle_time TEXT NOT NULL,

            last_signal_direction TEXT,

            updated_at TEXT NOT NULL,

            PRIMARY KEY (
                symbol,
                signal_interval,
                shadow_version
            )
        )
        """
    )


def _state_table_is_versioned(
    connection,
):
    info = _get_table_info(
        connection,
        "mtf_shadow_state",
    )

    if not info:
        return False

    columns = {
        str(
            row["name"]
        )
        for row in info
    }

    required_columns = {
        "symbol",
        "signal_interval",
        "shadow_version",
        "last_candle_time",
        "last_signal_direction",
        "updated_at",
    }

    if not required_columns.issubset(
        columns
    ):
        return False

    primary_key_columns = [
        str(
            row["name"]
        )
        for row in sorted(
            (
                row
                for row in info
                if int(
                    row["pk"]
                    or 0
                ) > 0
            ),
            key=lambda row: int(
                row["pk"]
            ),
        )
    ]

    return (
        primary_key_columns
        == [
            "symbol",
            "signal_interval",
            "shadow_version",
        ]
    )


def _migrate_legacy_state_table(
    connection,
):
    legacy_table = (
        "mtf_shadow_state_legacy_migration"
    )

    existing = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE
            type = 'table'
            AND name = ?
        """,
        (
            legacy_table,
        ),
    ).fetchone()

    if existing is not None:
        raise RuntimeError(
            "Legacy MTF shadow state migration "
            "table already exists"
        )

    connection.execute(
        f"""
        ALTER TABLE mtf_shadow_state
        RENAME TO {legacy_table}
        """
    )

    _create_versioned_state_table(
        connection
    )

    legacy_columns = _get_table_columns(
        connection,
        legacy_table,
    )

    required_legacy_columns = {
        "symbol",
        "signal_interval",
        "last_candle_time",
        "last_signal_direction",
        "updated_at",
    }

    if not required_legacy_columns.issubset(
        legacy_columns
    ):
        raise RuntimeError(
            "Legacy MTF shadow state schema "
            "is not recognized"
        )

    if "shadow_version" in legacy_columns:
        connection.execute(
            f"""
            INSERT INTO mtf_shadow_state (
                symbol,
                signal_interval,
                shadow_version,
                last_candle_time,
                last_signal_direction,
                updated_at
            )
            SELECT
                symbol,
                signal_interval,
                shadow_version,
                last_candle_time,
                last_signal_direction,
                updated_at
            FROM {legacy_table}
            """
        )

    else:
        connection.execute(
            f"""
            INSERT INTO mtf_shadow_state (
                symbol,
                signal_interval,
                shadow_version,
                last_candle_time,
                last_signal_direction,
                updated_at
            )
            SELECT
                symbol,
                signal_interval,
                ?,
                last_candle_time,
                last_signal_direction,
                updated_at
            FROM {legacy_table}
            """,
            (
                LEGACY_STATE_SHADOW_VERSION,
            ),
        )

    connection.execute(
        f"""
        DROP TABLE {legacy_table}
        """
    )


def _ensure_versioned_state_table(
    connection,
):
    info = _get_table_info(
        connection,
        "mtf_shadow_state",
    )

    if not info:
        _create_versioned_state_table(
            connection
        )
        return

    if _state_table_is_versioned(
        connection
    ):
        return

    _migrate_legacy_state_table(
        connection
    )


def init_mtf_shadow_storage(
    db_path=None,
):
    connection = _connect(
        db_path
    )

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS mtf_shadow_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                created_at TEXT NOT NULL,

                symbol TEXT NOT NULL,

                signal_interval TEXT NOT NULL,

                signal_candle_time TEXT NOT NULL,

                signal_close_time TEXT NOT NULL,

                signal_direction TEXT NOT NULL,

                signal_candidate_direction TEXT,

                entry_price REAL NOT NULL,

                signal_atr REAL,

                strategy_status TEXT,

                setup_score INTEGER,

                strategy_reason TEXT,

                context_interval TEXT NOT NULL,

                context_candle_time TEXT NOT NULL,

                context_close_time TEXT NOT NULL,

                context_direction TEXT,

                context_candidate_direction TEXT,

                direction_alignment TEXT NOT NULL,

                shadow_version TEXT NOT NULL
            )
            """
        )

        _ensure_signal_atr_column(
            connection
        )

        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_mtf_shadow_signal_unique
            ON mtf_shadow_signals (
                symbol,
                signal_interval,
                signal_candle_time,
                shadow_version
            )
            """
        )

        _ensure_versioned_state_table(
            connection
        )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def _get_state(
    connection,
    symbol,
):
    return connection.execute(
        """
        SELECT
            symbol,
            signal_interval,
            shadow_version,
            last_candle_time,
            last_signal_direction,
            updated_at
        FROM mtf_shadow_state
        WHERE
            symbol = ?
            AND signal_interval = ?
            AND shadow_version = ?
        """,
        (
            symbol,
            SIGNAL_INTERVAL,
            SHADOW_VERSION,
        ),
    ).fetchone()


def _save_state(
    connection,
    *,
    symbol,
    candle_time,
    signal_direction,
    created_at,
):
    connection.execute(
        """
        INSERT INTO mtf_shadow_state (
            symbol,
            signal_interval,
            shadow_version,
            last_candle_time,
            last_signal_direction,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)

        ON CONFLICT(
            symbol,
            signal_interval,
            shadow_version
        )
        DO UPDATE SET
            last_candle_time =
                excluded.last_candle_time,

            last_signal_direction =
                excluded.last_signal_direction,

            updated_at =
                excluded.updated_at
        """,
        (
            symbol,
            SIGNAL_INTERVAL,
            SHADOW_VERSION,
            candle_time,
            signal_direction,
            created_at,
        ),
    )


def _insert_shadow_signal(
    connection,
    *,
    symbol,
    mtf_result,
    created_at,
):
    signal_result = (
        mtf_result.get(
            "signal_result"
        )
        or {}
    )

    direction = (
        _normalize_direction(
            mtf_result.get(
                "signal_direction"
            )
        )
    )

    if direction is None:
        return (
            False,
            None,
        )

    entry_price = (
        _optional_float(
            signal_result.get(
                "close"
            )
        )
    )

    if entry_price is None:
        raise ValueError(
            "M30 shadow signal "
            "has no valid entry price"
        )

    signal_atr = (
        _optional_float(
            signal_result.get(
                "atr"
            )
        )
    )

    if (
        signal_atr is None
        or signal_atr <= 0
    ):
        raise ValueError(
            "M30 shadow signal "
            "has no valid ATR"
        )

    cursor = connection.execute(
        """
        INSERT INTO mtf_shadow_signals (
            created_at,
            symbol,
            signal_interval,
            signal_candle_time,
            signal_close_time,
            signal_direction,
            signal_candidate_direction,
            entry_price,
            signal_atr,
            strategy_status,
            setup_score,
            strategy_reason,
            context_interval,
            context_candle_time,
            context_close_time,
            context_direction,
            context_candidate_direction,
            direction_alignment,
            shadow_version
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?
        )

        ON CONFLICT(
            symbol,
            signal_interval,
            signal_candle_time,
            shadow_version
        )
        DO NOTHING
        """,
        (
            created_at,
            symbol,
            mtf_result.get(
                "signal_interval",
                SIGNAL_INTERVAL,
            ),
            mtf_result[
                "signal_candle_time"
            ],
            mtf_result[
                "signal_close_time"
            ],
            direction,
            _normalize_direction(
                mtf_result.get(
                    "signal_candidate_direction"
                )
            ),
            entry_price,
            signal_atr,
            signal_result.get(
                "status"
            ),
            _optional_int(
                signal_result.get(
                    "setup_score"
                )
            ),
            signal_result.get(
                "reason"
            ),
            mtf_result[
                "context_interval"
            ],
            mtf_result[
                "context_candle_time"
            ],
            mtf_result[
                "context_close_time"
            ],
            _normalize_direction(
                mtf_result.get(
                    "context_direction"
                )
            ),
            _normalize_direction(
                mtf_result.get(
                    "context_candidate_direction"
                )
            ),
            _normalize_alignment(
                mtf_result.get(
                    "direction_alignment"
                )
            ),
            SHADOW_VERSION,
        ),
    )

    if cursor.rowcount <= 0:
        return (
            False,
            None,
        )

    return (
        True,
        cursor.lastrowid,
    )


def process_mtf_shadow_signal(
    *,
    symbol,
    mtf_result,
    created_at,
    db_path=None,
):
    if not isinstance(
        mtf_result,
        dict,
    ):
        return {
            "action": "INVALID_RESULT",
            "created": False,
            "signal_id": None,
        }

    if not mtf_result.get(
        "ready"
    ):
        return {
            "action": "NOT_READY",
            "created": False,
            "signal_id": None,
        }

    candle_time = str(
        mtf_result[
            "signal_candle_time"
        ]
    )

    current_direction = (
        _normalize_direction(
            mtf_result.get(
                "signal_direction"
            )
        )
    )

    connection = _connect(
        db_path
    )

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        state = _get_state(
            connection,
            symbol,
        )

        if state is None:
            _save_state(
                connection,
                symbol=symbol,
                candle_time=candle_time,
                signal_direction=current_direction,
                created_at=created_at,
            )

            connection.commit()

            return {
                "action": "BASELINE",
                "created": False,
                "signal_id": None,
                "direction": current_direction,
                "candle_time": candle_time,
            }

        previous_candle_time = str(
            state[
                "last_candle_time"
            ]
        )

        previous_direction = (
            _normalize_direction(
                state[
                    "last_signal_direction"
                ]
            )
        )

        current_dt = _parse_time(
            candle_time
        )

        previous_dt = _parse_time(
            previous_candle_time
        )

        if current_dt < previous_dt:
            connection.rollback()

            return {
                "action": "STALE_CANDLE",
                "created": False,
                "signal_id": None,
                "direction": current_direction,
                "candle_time": candle_time,
            }

        if current_dt == previous_dt:
            connection.rollback()

            return {
                "action": "ALREADY_PROCESSED",
                "created": False,
                "signal_id": None,
                "direction": current_direction,
                "candle_time": candle_time,
            }

        gap_minutes = (
            current_dt
            - previous_dt
        ).total_seconds() / 60.0

        if (
            gap_minutes
            > SIGNAL_INTERVAL_MINUTES
        ):
            _save_state(
                connection,
                symbol=symbol,
                candle_time=candle_time,
                signal_direction=current_direction,
                created_at=created_at,
            )

            connection.commit()

            return {
                "action": "GAP_BASELINE",
                "created": False,
                "signal_id": None,
                "direction": current_direction,
                "candle_time": candle_time,
                "gap_minutes": gap_minutes,
            }

        _save_state(
            connection,
            symbol=symbol,
            candle_time=candle_time,
            signal_direction=current_direction,
            created_at=created_at,
        )

        if current_direction is None:
            connection.commit()

            return {
                "action": "NO_SIGNAL",
                "created": False,
                "signal_id": None,
                "previous_direction": previous_direction,
                "direction": None,
                "candle_time": candle_time,
            }

        if (
            current_direction
            == previous_direction
        ):
            connection.commit()

            return {
                "action": "CONTINUATION",
                "created": False,
                "signal_id": None,
                "previous_direction": previous_direction,
                "direction": current_direction,
                "candle_time": candle_time,
            }

        (
            created,
            signal_id,
        ) = _insert_shadow_signal(
            connection,
            symbol=symbol,
            mtf_result=mtf_result,
            created_at=created_at,
        )

        connection.commit()

        if not created:
            return {
                "action": "DUPLICATE",
                "created": False,
                "signal_id": None,
                "previous_direction": previous_direction,
                "direction": current_direction,
                "candle_time": candle_time,
            }

        return {
            "action": "NEW_SIGNAL",
            "created": True,
            "signal_id": signal_id,
            "previous_direction": previous_direction,
            "direction": current_direction,
            "candle_time": candle_time,
        }

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def backfill_mtf_shadow_signal_atr(
    *,
    signal_id,
    signal_atr,
    db_path=None,
):
    atr = _optional_float(
        signal_atr
    )

    if (
        atr is None
        or atr <= 0
    ):
        raise ValueError(
            "signal_atr must be "
            "a positive finite number"
        )

    connection = _connect(
        db_path
    )

    try:
        cursor = connection.execute(
            """
            UPDATE mtf_shadow_signals
            SET signal_atr = ?
            WHERE
                id = ?
                AND signal_atr IS NULL
            """,
            (
                atr,
                int(
                    signal_id
                ),
            ),
        )

        connection.commit()

        return (
            cursor.rowcount
            > 0
        )

    finally:
        connection.close()


def get_mtf_shadow_signal(
    signal_id,
    db_path=None,
):
    connection = _connect(
        db_path
    )

    try:
        row = connection.execute(
            """
            SELECT *
            FROM mtf_shadow_signals
            WHERE id = ?
            """,
            (
                int(
                    signal_id
                ),
            ),
        ).fetchone()

        if row is None:
            return None

        return dict(
            row
        )

    finally:
        connection.close()


def count_mtf_shadow_signals(
    symbol=None,
    db_path=None,
):
    connection = _connect(
        db_path
    )

    try:
        if symbol is None:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM mtf_shadow_signals
                """
            ).fetchone()

        else:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM mtf_shadow_signals
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

    finally:
        connection.close()
