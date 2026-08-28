import os
import sqlite3

from datetime import (
    datetime,
    timedelta,
)
from math import isfinite

from config import (
    STOP_LOSS_ATR_MULTIPLIER,
    TAKE_PROFIT_R_MULTIPLE,
    MAX_TRADE_MINUTES,
    get_instrument_config,
)


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

SIGNAL_INTERVAL = "30min"

EXECUTION_INTERVAL = "5min"
EXECUTION_INTERVAL_MINUTES = 5

SHADOW_TRADE_MODEL_VERSION = (
    "M30H1_SHADOW_TRADE_V1"
)

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
    return db_path or DEFAULT_DB_PATH


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


def _parse_time(
    value,
):
    return datetime.strptime(
        str(value),
        TIME_FORMAT,
    )


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


def init_mtf_shadow_trade_storage(
    db_path=None,
):
    connection = _connect(
        db_path
    )

    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS mtf_shadow_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                shadow_signal_id INTEGER NOT NULL UNIQUE,

                created_at TEXT NOT NULL,

                symbol TEXT NOT NULL,

                signal_interval TEXT NOT NULL,

                execution_interval TEXT NOT NULL,

                signal TEXT NOT NULL,

                entry_time TEXT NOT NULL,

                entry_price REAL NOT NULL,

                atr REAL NOT NULL,

                stop_loss REAL NOT NULL,

                take_profit REAL NOT NULL,

                risk_pips REAL NOT NULL,

                reward_pips REAL NOT NULL,

                spread_pips REAL NOT NULL,

                max_hold_minutes INTEGER NOT NULL,

                model_version TEXT NOT NULL,

                status TEXT NOT NULL DEFAULT 'OPEN',

                exit_candle_time TEXT,

                exit_price REAL,

                exit_reason TEXT,

                gross_pnl_pips REAL,

                net_pnl_pips REAL,

                r_multiple REAL,

                mae_pips REAL,

                mfe_pips REAL
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_mtf_shadow_trades_symbol_status
            ON mtf_shadow_trades (
                symbol,
                status
            )
            """
        )

        connection.commit()

    finally:
        connection.close()


def create_mtf_shadow_trade(
    *,
    symbol,
    shadow_result,
    mtf_result,
    created_at,
    db_path=None,
):
    """
    Create one simulated M30 shadow trade
    only for an actual shadow NEW_SIGNAL.

    Signal:
    M30.

    Execution tracking:
    closed 5min candles.

    No Telegram.
    No broker execution.
    No trading.db.
    """

    if not isinstance(
        shadow_result,
        dict,
    ):
        return {
            "created": False,
            "reason": "INVALID_SHADOW_RESULT",
            "trade_id": None,
        }

    if not shadow_result.get(
        "created"
    ):
        return {
            "created": False,
            "reason": "NO_NEW_SHADOW_SIGNAL",
            "trade_id": None,
        }

    if (
        shadow_result.get(
            "action"
        )
        != "NEW_SIGNAL"
    ):
        return {
            "created": False,
            "reason": "NOT_NEW_SIGNAL_ACTION",
            "trade_id": None,
        }

    if not isinstance(
        mtf_result,
        dict,
    ):
        return {
            "created": False,
            "reason": "INVALID_MTF_RESULT",
            "trade_id": None,
        }

    if not mtf_result.get(
        "ready"
    ):
        return {
            "created": False,
            "reason": "MTF_NOT_READY",
            "trade_id": None,
        }

    shadow_signal_id = (
        shadow_result.get(
            "signal_id"
        )
    )

    if shadow_signal_id is None:
        return {
            "created": False,
            "reason": "MISSING_SHADOW_SIGNAL_ID",
            "trade_id": None,
        }

    signal = _normalize_direction(
        mtf_result.get(
            "signal_direction"
        )
    )

    if signal is None:
        return {
            "created": False,
            "reason": "NO_VALID_SIGNAL_DIRECTION",
            "trade_id": None,
        }

    signal_result = (
        mtf_result.get(
            "signal_result"
        )
        or {}
    )

    entry_price = _optional_float(
        signal_result.get(
            "close"
        )
    )

    atr = _optional_float(
        signal_result.get(
            "atr"
        )
    )

    if (
        entry_price is None
        or atr is None
        or atr <= 0
    ):
        raise ValueError(
            "M30 shadow trade has invalid "
            "entry price or ATR"
        )

    entry_time = str(
        mtf_result[
            "signal_close_time"
        ]
    )

    _parse_time(
        entry_time
    )

    instrument = (
        get_instrument_config(
            symbol
        )
    )

    pip_size = _optional_float(
        instrument.get(
            "pip_size"
        )
    )

    min_stop_pips = _optional_float(
        instrument.get(
            "min_stop_pips"
        )
    )

    spread_pips = _optional_float(
        instrument.get(
            "assumed_spread_pips"
        )
    )

    if (
        pip_size is None
        or pip_size <= 0
        or min_stop_pips is None
        or min_stop_pips < 0
        or spread_pips is None
        or spread_pips < 0
    ):
        raise ValueError(
            "Invalid instrument execution "
            "configuration"
        )

    stop_multiplier = _optional_float(
        STOP_LOSS_ATR_MULTIPLIER
    )

    reward_multiple = _optional_float(
        TAKE_PROFIT_R_MULTIPLE
    )

    if (
        stop_multiplier is None
        or stop_multiplier <= 0
        or reward_multiple is None
        or reward_multiple <= 0
    ):
        raise ValueError(
            "Invalid shadow trade "
            "risk/reward configuration"
        )

    try:
        max_hold_minutes = int(
            MAX_TRADE_MINUTES
        )

    except (
        TypeError,
        ValueError,
    ):
        raise ValueError(
            "Invalid MAX_TRADE_MINUTES"
        )

    if max_hold_minutes <= 0:
        raise ValueError(
            "MAX_TRADE_MINUTES must be > 0"
        )

    atr_stop_distance = (
        atr
        * stop_multiplier
    )

    minimum_stop_distance = (
        min_stop_pips
        * pip_size
    )

    stop_distance = max(
        atr_stop_distance,
        minimum_stop_distance,
    )

    target_distance = (
        stop_distance
        * reward_multiple
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

    connection = _connect(
        db_path
    )

    try:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO mtf_shadow_trades (
                shadow_signal_id,
                created_at,
                symbol,
                signal_interval,
                execution_interval,
                signal,
                entry_time,
                entry_price,
                atr,
                stop_loss,
                take_profit,
                risk_pips,
                reward_pips,
                spread_pips,
                max_hold_minutes,
                model_version,
                status,
                mae_pips,
                mfe_pips
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                'OPEN', 0.0, 0.0
            )
            """,
            (
                int(
                    shadow_signal_id
                ),

                created_at,
                symbol,

                mtf_result.get(
                    "signal_interval",
                    SIGNAL_INTERVAL,
                ),

                EXECUTION_INTERVAL,

                signal,
                entry_time,
                entry_price,
                atr,
                stop_loss,
                take_profit,
                risk_pips,
                reward_pips,
                spread_pips,
                max_hold_minutes,
                SHADOW_TRADE_MODEL_VERSION,
            ),
        )

        connection.commit()

        if cursor.rowcount <= 0:
            return {
                "created": False,
                "reason": "DUPLICATE",
                "trade_id": None,
            }

        return {
            "created": True,
            "reason": "CREATED",

            "trade_id":
                cursor.lastrowid,

            "shadow_signal_id":
                int(
                    shadow_signal_id
                ),

            "symbol":
                symbol,

            "signal":
                signal,

            "entry_time":
                entry_time,

            "entry_price":
                entry_price,

            "stop_loss":
                stop_loss,

            "take_profit":
                take_profit,

            "risk_pips":
                risk_pips,

            "reward_pips":
                reward_pips,

            "spread_pips":
                spread_pips,

            "max_hold_minutes":
                max_hold_minutes,
        }

    finally:
        connection.close()


def _get_open_trades(
    connection,
    symbol,
):
    return connection.execute(
        """
        SELECT *
        FROM mtf_shadow_trades
        WHERE
            symbol = ?
            AND status = 'OPEN'
        ORDER BY id
        """,
        (
            symbol,
        ),
    ).fetchall()


def _update_excursions(
    connection,
    *,
    trade_id,
    mae_pips,
    mfe_pips,
):
    connection.execute(
        """
        UPDATE mtf_shadow_trades
        SET
            mae_pips = ?,
            mfe_pips = ?
        WHERE
            id = ?
            AND status = 'OPEN'
        """,
        (
            mae_pips,
            mfe_pips,
            trade_id,
        ),
    )


def _close_trade(
    connection,
    *,
    trade_id,
    status,
    exit_candle_time,
    exit_price,
    exit_reason,
    gross_pnl_pips,
    net_pnl_pips,
    r_multiple,
    mae_pips,
    mfe_pips,
):
    cursor = connection.execute(
        """
        UPDATE mtf_shadow_trades
        SET
            status = ?,
            exit_candle_time = ?,
            exit_price = ?,
            exit_reason = ?,
            gross_pnl_pips = ?,
            net_pnl_pips = ?,
            r_multiple = ?,
            mae_pips = ?,
            mfe_pips = ?
        WHERE
            id = ?
            AND status = 'OPEN'
        """,
        (
            status,
            exit_candle_time,
            exit_price,
            exit_reason,
            gross_pnl_pips,
            net_pnl_pips,
            r_multiple,
            mae_pips,
            mfe_pips,
            trade_id,
        ),
    )

    return cursor.rowcount > 0


def _calculate_directional_pips(
    *,
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


def _calculate_candle_excursions(
    *,
    signal,
    entry_price,
    high,
    low,
    pip_size,
):
    if signal == "BUY":
        adverse_pips = max(
            (
                entry_price
                - low
            )
            / pip_size,
            0.0,
        )

        favorable_pips = max(
            (
                high
                - entry_price
            )
            / pip_size,
            0.0,
        )

    else:
        adverse_pips = max(
            (
                high
                - entry_price
            )
            / pip_size,
            0.0,
        )

        favorable_pips = max(
            (
                entry_price
                - low
            )
            / pip_size,
            0.0,
        )

    return (
        adverse_pips,
        favorable_pips,
    )


def _sorted_execution_candles(
    five_minute_candles,
):
    return sorted(
        five_minute_candles,
        key=lambda candle: _parse_time(
            candle[
                "datetime"
            ]
        ),
    )


def evaluate_mtf_shadow_trades(
    five_minute_candles,
    symbol,
    db_path=None,
):
    """
    Evaluate open M30 shadow trades
    using closed 5-minute candles.

    Signal timeframe:
    30min.

    Execution timeframe:
    5min.

    TIME RULE:
    No candle whose close is later than
    maximum_exit_time may affect SL/TP.

    If market-data history has already
    advanced beyond the deadline but
    there is a gap at the timeout,
    the trade exits using the LAST
    available closed 5m candle whose
    close was <= maximum_exit_time.

    Such an exit is labelled:
    TIMEOUT_GAP.

    EXIT-CANDLE MAE/MFE RULE:

    TP:
    MFE is exactly the reward distance.
    Final exact MAE is unknown from OHLC
    because intrabar order is unknown,
    so MAE is stored as NULL.

    SL:
    MAE is exactly the risk distance.
    Final exact MFE is unknown from OHLC,
    so MFE is stored as NULL.

    AMBIGUOUS:
    both are NULL.

    TIMEOUT:
    full candle High/Low are valid because
    the position remains open through the
    candle until its close.

    Cost model:
    gross P/L - assumed spread.

    Commission/slippage are not invented.
    """

    if not five_minute_candles:
        return []

    execution_candles = (
        _sorted_execution_candles(
            five_minute_candles
        )
    )

    instrument = (
        get_instrument_config(
            symbol
        )
    )

    pip_size = _optional_float(
        instrument.get(
            "pip_size"
        )
    )

    if (
        pip_size is None
        or pip_size <= 0
    ):
        raise ValueError(
            "Invalid pip_size"
        )

    connection = _connect(
        db_path
    )

    results = []

    try:
        open_trades = (
            _get_open_trades(
                connection,
                symbol,
            )
        )

        if not open_trades:
            return []

        latest_available_close_time = (
            _parse_time(
                execution_candles[-1][
                    "datetime"
                ]
            )
            + timedelta(
                minutes=(
                    EXECUTION_INTERVAL_MINUTES
                )
            )
        )

        for trade in open_trades:
            entry_time = _parse_time(
                trade[
                    "entry_time"
                ]
            )

            maximum_exit_time = (
                entry_time
                + timedelta(
                    minutes=int(
                        trade[
                            "max_hold_minutes"
                        ]
                    )
                )
            )

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

            signal = str(
                trade[
                    "signal"
                ]
            )

            current_mae = float(
                trade[
                    "mae_pips"
                ]
                or 0.0
            )

            current_mfe = float(
                trade[
                    "mfe_pips"
                ]
                or 0.0
            )

            trade_closed = False

            last_eligible_candle = None
            last_eligible_close_time = None
            last_eligible_close_price = None

            for candle in execution_candles:
                candle_open = _parse_time(
                    candle[
                        "datetime"
                    ]
                )

                if (
                    candle_open
                    < entry_time
                ):
                    continue

                candle_close_time = (
                    candle_open
                    + timedelta(
                        minutes=(
                            EXECUTION_INTERVAL_MINUTES
                        )
                    )
                )

                # A candle that extends beyond
                # the trade deadline cannot
                # affect SL/TP.
                if (
                    candle_close_time
                    > maximum_exit_time
                ):
                    break

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

                last_eligible_candle = candle
                last_eligible_close_time = (
                    candle_close_time
                )
                last_eligible_close_price = close

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

                # Both barriers touched.
                # OHLC cannot determine which
                # happened first.
                if (
                    stop_hit
                    and target_hit
                ):
                    closed = _close_trade(
                        connection,

                        trade_id=(
                            trade[
                                "id"
                            ]
                        ),

                        status="AMBIGUOUS",

                        exit_candle_time=(
                            candle[
                                "datetime"
                            ]
                        ),

                        exit_price=None,

                        exit_reason=(
                            "SL_AND_TP_SAME_CANDLE"
                        ),

                        gross_pnl_pips=None,
                        net_pnl_pips=None,
                        r_multiple=None,

                        mae_pips=None,
                        mfe_pips=None,
                    )

                    if closed:
                        results.append(
                            {
                                "trade_id":
                                    trade[
                                        "id"
                                    ],

                                "shadow_signal_id":
                                    trade[
                                        "shadow_signal_id"
                                    ],

                                "symbol":
                                    symbol,

                                "signal":
                                    signal,

                                "result":
                                    "AMBIGUOUS",

                                "candle_time":
                                    candle[
                                        "datetime"
                                    ],

                                "gross_pips":
                                    None,

                                "net_pips":
                                    None,

                                "r":
                                    None,

                                "mae_pips":
                                    None,

                                "mfe_pips":
                                    None,
                            }
                        )

                    trade_closed = True
                    break

                # TP is known to be reached,
                # therefore MFE while the trade
                # is alive is exactly reward_pips.
                #
                # Exact final MAE cannot be
                # reconstructed from OHLC because
                # the candle Low may have occurred
                # before or after TP.
                if target_hit:
                    exact_mfe = (
                        reward_pips
                    )

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

                    closed = _close_trade(
                        connection,

                        trade_id=(
                            trade[
                                "id"
                            ]
                        ),

                        status="CLOSED",

                        exit_candle_time=(
                            candle[
                                "datetime"
                            ]
                        ),

                        exit_price=(
                            take_profit
                        ),

                        exit_reason="TAKE_PROFIT",

                        gross_pnl_pips=(
                            gross_pips
                        ),

                        net_pnl_pips=(
                            net_pips
                        ),

                        r_multiple=(
                            r_multiple
                        ),

                        mae_pips=None,

                        mfe_pips=(
                            exact_mfe
                        ),
                    )

                    if closed:
                        results.append(
                            {
                                "trade_id":
                                    trade[
                                        "id"
                                    ],

                                "shadow_signal_id":
                                    trade[
                                        "shadow_signal_id"
                                    ],

                                "symbol":
                                    symbol,

                                "signal":
                                    signal,

                                "result":
                                    "TAKE_PROFIT",

                                "candle_time":
                                    candle[
                                        "datetime"
                                    ],

                                "gross_pips":
                                    gross_pips,

                                "net_pips":
                                    net_pips,

                                "r":
                                    r_multiple,

                                "mae_pips":
                                    None,

                                "mfe_pips":
                                    exact_mfe,
                            }
                        )

                    trade_closed = True
                    break

                # SL is known to be reached,
                # therefore MAE while the trade
                # is alive is exactly risk_pips.
                #
                # Exact final MFE cannot be
                # reconstructed from OHLC because
                # the candle High/Low on the
                # favorable side may have occurred
                # before or after SL.
                if stop_hit:
                    exact_mae = (
                        risk_pips
                    )

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

                    closed = _close_trade(
                        connection,

                        trade_id=(
                            trade[
                                "id"
                            ]
                        ),

                        status="CLOSED",

                        exit_candle_time=(
                            candle[
                                "datetime"
                            ]
                        ),

                        exit_price=(
                            stop_loss
                        ),

                        exit_reason="STOP_LOSS",

                        gross_pnl_pips=(
                            gross_pips
                        ),

                        net_pnl_pips=(
                            net_pips
                        ),

                        r_multiple=(
                            r_multiple
                        ),

                        mae_pips=(
                            exact_mae
                        ),

                        mfe_pips=None,
                    )

                    if closed:
                        results.append(
                            {
                                "trade_id":
                                    trade[
                                        "id"
                                    ],

                                "shadow_signal_id":
                                    trade[
                                        "shadow_signal_id"
                                    ],

                                "symbol":
                                    symbol,

                                "signal":
                                    signal,

                                "result":
                                    "STOP_LOSS",

                                "candle_time":
                                    candle[
                                        "datetime"
                                    ],

                                "gross_pips":
                                    gross_pips,

                                "net_pips":
                                    net_pips,

                                "r":
                                    r_multiple,

                                "mae_pips":
                                    exact_mae,

                                "mfe_pips":
                                    None,
                            }
                        )

                    trade_closed = True
                    break

                # The trade remained open
                # throughout this whole candle,
                # so full candle excursions are
                # valid.
                (
                    candle_mae,
                    candle_mfe,
                ) = (
                    _calculate_candle_excursions(
                        signal=signal,

                        entry_price=(
                            entry_price
                        ),

                        high=high,
                        low=low,

                        pip_size=(
                            pip_size
                        ),
                    )
                )

                current_mae = max(
                    current_mae,
                    candle_mae,
                )

                current_mfe = max(
                    current_mfe,
                    candle_mfe,
                )

                _update_excursions(
                    connection,

                    trade_id=(
                        trade[
                            "id"
                        ]
                    ),

                    mae_pips=(
                        current_mae
                    ),

                    mfe_pips=(
                        current_mfe
                    ),
                )

            if trade_closed:
                continue

            # Do not TIMEOUT until the available
            # closed market-data history has
            # actually reached/passed the deadline.
            if (
                latest_available_close_time
                < maximum_exit_time
            ):
                continue

            # Deadline has passed but there was
            # no usable 5m execution candle at all
            # after the M30 entry.
            #
            # Do not invent an exit price.
            if last_eligible_candle is None:
                closed = _close_trade(
                    connection,

                    trade_id=(
                        trade[
                            "id"
                        ]
                    ),

                    status="DATA_GAP",

                    exit_candle_time=None,
                    exit_price=None,

                    exit_reason=(
                        "NO_EXECUTION_DATA_"
                        "BEFORE_TIMEOUT"
                    ),

                    gross_pnl_pips=None,
                    net_pnl_pips=None,
                    r_multiple=None,

                    mae_pips=None,
                    mfe_pips=None,
                )

                if closed:
                    results.append(
                        {
                            "trade_id":
                                trade[
                                    "id"
                                ],

                            "shadow_signal_id":
                                trade[
                                    "shadow_signal_id"
                                ],

                            "symbol":
                                symbol,

                            "signal":
                                signal,

                            "result":
                                "DATA_GAP",

                            "candle_time":
                                None,

                            "gross_pips":
                                None,

                            "net_pips":
                                None,

                            "r":
                                None,

                            "mae_pips":
                                None,

                            "mfe_pips":
                                None,
                        }
                    )

                continue

            gross_pips = (
                _calculate_directional_pips(
                    signal=signal,

                    entry_price=(
                        entry_price
                    ),

                    exit_price=(
                        last_eligible_close_price
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

            if (
                last_eligible_close_time
                == maximum_exit_time
            ):
                exit_reason = (
                    "TIMEOUT"
                )

            else:
                exit_reason = (
                    "TIMEOUT_GAP"
                )

            closed = _close_trade(
                connection,

                trade_id=(
                    trade[
                        "id"
                    ]
                ),

                status="CLOSED",

                exit_candle_time=(
                    last_eligible_candle[
                        "datetime"
                    ]
                ),

                exit_price=(
                    last_eligible_close_price
                ),

                exit_reason=(
                    exit_reason
                ),

                gross_pnl_pips=(
                    gross_pips
                ),

                net_pnl_pips=(
                    net_pips
                ),

                r_multiple=(
                    r_multiple
                ),

                mae_pips=(
                    current_mae
                ),

                mfe_pips=(
                    current_mfe
                ),
            )

            if closed:
                results.append(
                    {
                        "trade_id":
                            trade[
                                "id"
                            ],

                        "shadow_signal_id":
                            trade[
                                "shadow_signal_id"
                            ],

                        "symbol":
                            symbol,

                        "signal":
                            signal,

                        "result":
                            exit_reason,

                        "candle_time":
                            last_eligible_candle[
                                "datetime"
                            ],

                        "gross_pips":
                            gross_pips,

                        "net_pips":
                            net_pips,

                        "r":
                            r_multiple,

                        "mae_pips":
                            current_mae,

                        "mfe_pips":
                            current_mfe,
                    }
                )

        connection.commit()

        return results

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def count_mtf_shadow_trades(
    symbol=None,
    status=None,
    db_path=None,
):
    connection = _connect(
        db_path
    )

    try:
        query = (
            "SELECT COUNT(*) AS total "
            "FROM mtf_shadow_trades "
            "WHERE 1=1"
        )

        params = []

        if symbol is not None:
            query += (
                " AND symbol = ?"
            )

            params.append(
                symbol
            )

        if status is not None:
            query += (
                " AND status = ?"
            )

            params.append(
                status
            )

        row = connection.execute(
            query,
            params,
        ).fetchone()

        return int(
            row[
                "total"
            ]
            or 0
        )

    finally:
        connection.close()
