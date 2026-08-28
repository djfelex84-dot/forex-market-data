import os
import sqlite3

from math import (
    isfinite,
)

from mtf_shadow_trades import (
    create_mtf_shadow_trade,
)


SUPPORTED_SHADOW_VERSION = (
    "M30H1_SHADOW_V1"
)

SUPPORTED_SIGNAL_INTERVAL = "30min"

DEFAULT_DB_PATH = os.getenv(
    "MTF_SHADOW_DB_PATH",
    "/app/data/mtf_shadow_signals.db",
)

VALID_DIRECTIONS = {
    "BUY",
    "SELL",
}


def _resolve_db_path(
    db_path=None,
):
    return (
        db_path
        or DEFAULT_DB_PATH
    )


def _connect(
    db_path=None,
):
    connection = sqlite3.connect(
        _resolve_db_path(
            db_path
        )
    )

    connection.row_factory = (
        sqlite3.Row
    )

    return connection


def _positive_float(
    value,
):
    try:
        number = float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        return None

    if (
        not isfinite(
            number
        )
        or number <= 0
    ):
        return None

    return number


def find_mtf_shadow_signals_without_trades(
    symbol=None,
    db_path=None,
):
    """
    Return persisted shadow signals that
    do not yet have a shadow trade.

    mtf_shadow_signals contains only actual
    NEW_SIGNAL events, so there is no action
    column to filter.

    Only the currently supported signal
    version/timeframe is reconciled.
    """

    connection = _connect(
        db_path
    )

    try:
        query = """
            SELECT
                s.*
            FROM mtf_shadow_signals AS s

            LEFT JOIN mtf_shadow_trades AS t
                ON t.shadow_signal_id = s.id

            WHERE
                t.shadow_signal_id IS NULL

                AND s.shadow_version = ?

                AND s.signal_interval = ?
        """

        params = [
            SUPPORTED_SHADOW_VERSION,
            SUPPORTED_SIGNAL_INTERVAL,
        ]

        if symbol is not None:
            query += (
                " AND s.symbol = ?"
            )

            params.append(
                symbol
            )

        query += (
            " ORDER BY s.id"
        )

        rows = connection.execute(
            query,
            params,
        ).fetchall()

        return [
            dict(
                row
            )
            for row in rows
        ]

    finally:
        connection.close()


def _build_replay_payload(
    signal_row,
):
    signal_id = signal_row.get(
        "id"
    )

    symbol = signal_row.get(
        "symbol"
    )

    signal_interval = signal_row.get(
        "signal_interval"
    )

    shadow_version = signal_row.get(
        "shadow_version"
    )

    direction = str(
        signal_row.get(
            "signal_direction"
        )
        or ""
    ).upper()

    entry_price = _positive_float(
        signal_row.get(
            "entry_price"
        )
    )

    signal_atr = _positive_float(
        signal_row.get(
            "signal_atr"
        )
    )

    signal_close_time = (
        signal_row.get(
            "signal_close_time"
        )
    )

    created_at = signal_row.get(
        "created_at"
    )

    if signal_id is None:
        return (
            None,
            "MISSING_SIGNAL_ID",
        )

    if not symbol:
        return (
            None,
            "MISSING_SYMBOL",
        )

    if (
        signal_interval
        != SUPPORTED_SIGNAL_INTERVAL
    ):
        return (
            None,
            "UNSUPPORTED_SIGNAL_INTERVAL",
        )

    if (
        shadow_version
        != SUPPORTED_SHADOW_VERSION
    ):
        return (
            None,
            "UNSUPPORTED_SHADOW_VERSION",
        )

    if direction not in VALID_DIRECTIONS:
        return (
            None,
            "INVALID_DIRECTION",
        )

    if entry_price is None:
        return (
            None,
            "INVALID_ENTRY_PRICE",
        )

    if signal_atr is None:
        return (
            None,
            "MISSING_OR_INVALID_SIGNAL_ATR",
        )

    if not signal_close_time:
        return (
            None,
            "MISSING_SIGNAL_CLOSE_TIME",
        )

    if not created_at:
        return (
            None,
            "MISSING_CREATED_AT",
        )

    shadow_result = {
        "created": True,
        "action": "NEW_SIGNAL",
        "signal_id": int(
            signal_id
        ),
    }

    mtf_result = {
        "ready": True,

        "signal_interval":
            signal_interval,

        "signal_close_time":
            signal_close_time,

        "signal_direction":
            direction,

        "signal_result": {
            "close":
                entry_price,

            "atr":
                signal_atr,
        },
    }

    return (
        {
            "symbol":
                symbol,

            "created_at":
                created_at,

            "shadow_result":
                shadow_result,

            "mtf_result":
                mtf_result,
        },
        None,
    )


def reconcile_mtf_shadow_trades(
    symbol=None,
    db_path=None,
):
    """
    Idempotently create missing downstream
    shadow trades from persisted shadow
    signals.

    The signal table is the source of truth.

    This makes trade creation recoverable
    after:
    - transient SQLite errors;
    - Python crashes;
    - container restarts;
    - server restarts;
    - interruption between signal COMMIT
      and trade INSERT.

    No Telegram.
    No broker execution.
    No market-data request.
    """

    orphan_signals = (
        find_mtf_shadow_signals_without_trades(
            symbol=symbol,
            db_path=db_path,
        )
    )

    items = []

    created_count = 0
    duplicate_count = 0
    error_count = 0

    for signal_row in orphan_signals:
        signal_id = signal_row.get(
            "id"
        )

        payload, validation_error = (
            _build_replay_payload(
                signal_row
            )
        )

        if validation_error is not None:
            error_count += 1

            items.append(
                {
                    "signal_id":
                        signal_id,

                    "created":
                        False,

                    "reason":
                        validation_error,
                }
            )

            continue

        try:
            result = (
                create_mtf_shadow_trade(
                    symbol=(
                        payload[
                            "symbol"
                        ]
                    ),

                    shadow_result=(
                        payload[
                            "shadow_result"
                        ]
                    ),

                    mtf_result=(
                        payload[
                            "mtf_result"
                        ]
                    ),

                    created_at=(
                        payload[
                            "created_at"
                        ]
                    ),

                    db_path=(
                        db_path
                    ),
                )
            )

        except Exception as error:
            error_count += 1

            items.append(
                {
                    "signal_id":
                        signal_id,

                    "created":
                        False,

                    "reason":
                        "CREATE_ERROR",

                    "error":
                        (
                            f"{type(error).__name__}: "
                            f"{error}"
                        ),
                }
            )

            continue

        if result.get(
            "created"
        ):
            created_count += 1

        elif (
            result.get(
                "reason"
            )
            == "DUPLICATE"
        ):
            duplicate_count += 1

        else:
            error_count += 1

        item = dict(
            result
        )

        item[
            "signal_id"
        ] = signal_id

        items.append(
            item
        )

    return {
        "checked":
            len(
                orphan_signals
            ),

        "created":
            created_count,

        "duplicates":
            duplicate_count,

        "errors":
            error_count,

        "items":
            items,
    }
