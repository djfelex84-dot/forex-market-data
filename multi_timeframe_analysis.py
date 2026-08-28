from datetime import (
    datetime,
    timedelta,
)

from signal_quality import (
    build_quality_snapshot,
)
from strategy import (
    analyze_market,
)
from timeframes import (
    build_signal_timeframes,
)


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

SIGNAL_INTERVAL = "30min"
CONTEXT_INTERVAL = "60min"

SIGNAL_INTERVAL_MINUTES = 30
CONTEXT_INTERVAL_MINUTES = 60

MIN_SIGNAL_CANDLES = 60
MIN_CONTEXT_CANDLES = 60

VALID_DIRECTIONS = {
    "BUY",
    "SELL",
}


def _parse_datetime(
    value,
):
    if isinstance(
        value,
        datetime,
    ):
        return value

    return datetime.strptime(
        value,
        TIME_FORMAT,
    )


def _candle_close_time(
    candle,
    interval_minutes,
):
    candle_time = _parse_datetime(
        candle["datetime"]
    )

    return (
        candle_time
        + timedelta(
            minutes=interval_minutes
        )
    )


def _signal_direction_from_result(
    result,
):
    if not isinstance(
        result,
        dict,
    ):
        return None

    signal = str(
        result.get(
            "signal",
            "",
        )
    ).upper()

    if signal in VALID_DIRECTIONS:
        return signal

    return None


def _candidate_direction_from_result(
    result,
):
    if not isinstance(
        result,
        dict,
    ):
        return None

    candidate = str(
        result.get(
            "candidate",
            "",
        )
    ).upper()

    if candidate in VALID_DIRECTIONS:
        return candidate

    return None


def _direction_alignment(
    signal_direction,
    context_direction,
):
    if (
        signal_direction
        not in VALID_DIRECTIONS
        or context_direction
        not in VALID_DIRECTIONS
    ):
        return "UNKNOWN"

    if (
        signal_direction
        == context_direction
    ):
        return "ALIGNED"

    return "CONFLICT"


def _not_ready_result(
    *,
    symbol,
    source_5m_count,
    signal_candle_count,
    context_candle_count,
    safe_context_candle_count,
    reason,
):
    return {
        "ready": False,
        "symbol": symbol,

        "signal_interval":
            SIGNAL_INTERVAL,

        "context_interval":
            CONTEXT_INTERVAL,

        "source_5m_count":
            source_5m_count,

        "signal_candle_count":
            signal_candle_count,

        "context_candle_count":
            context_candle_count,

        "safe_context_candle_count":
            safe_context_candle_count,

        "reason":
            reason,
    }


def _safe_context_history(
    context_candles,
    signal_close_time,
):
    safe = []

    for candle in context_candles:
        close_time = (
            _candle_close_time(
                candle,
                CONTEXT_INTERVAL_MINUTES,
            )
        )

        if (
            close_time
            <= signal_close_time
        ):
            safe.append(
                candle
            )

    return safe


def build_multi_timeframe_analysis(
    five_minute_candles,
    symbol=None,
):
    """
    Build a read-only multi-timeframe
    analysis from already closed 5-minute
    candles.

    30min is the signal timeframe.

    60min is the higher-timeframe context.

    The H1 context is explicitly limited
    to candles that were already closed
    when the latest M30 signal candle
    closed.

    This function does NOT:
    - publish Telegram messages;
    - create signal events;
    - create trades;
    - write SQLite;
    - call market-data APIs;
    - PASS or REJECT a trade.
    """

    source_5m_count = len(
        five_minute_candles
        or []
    )

    timeframes = (
        build_signal_timeframes(
            five_minute_candles
        )
    )

    signal_candles = (
        timeframes.get(
            SIGNAL_INTERVAL,
            [],
        )
    )

    context_candles = (
        timeframes.get(
            CONTEXT_INTERVAL,
            [],
        )
    )

    signal_candle_count = len(
        signal_candles
    )

    context_candle_count = len(
        context_candles
    )

    if (
        signal_candle_count
        < MIN_SIGNAL_CANDLES
    ):
        return _not_ready_result(
            symbol=symbol,
            source_5m_count=(
                source_5m_count
            ),
            signal_candle_count=(
                signal_candle_count
            ),
            context_candle_count=(
                context_candle_count
            ),
            safe_context_candle_count=0,
            reason=(
                "Not enough complete "
                "30min candles"
            ),
        )

    latest_signal_candle = (
        signal_candles[-1]
    )

    signal_close_time = (
        _candle_close_time(
            latest_signal_candle,
            SIGNAL_INTERVAL_MINUTES,
        )
    )

    safe_context_candles = (
        _safe_context_history(
            context_candles,
            signal_close_time,
        )
    )

    safe_context_candle_count = len(
        safe_context_candles
    )

    if (
        safe_context_candle_count
        < MIN_CONTEXT_CANDLES
    ):
        return _not_ready_result(
            symbol=symbol,
            source_5m_count=(
                source_5m_count
            ),
            signal_candle_count=(
                signal_candle_count
            ),
            context_candle_count=(
                context_candle_count
            ),
            safe_context_candle_count=(
                safe_context_candle_count
            ),
            reason=(
                "Not enough safe complete "
                "60min candles"
            ),
        )

    latest_context_candle = (
        safe_context_candles[-1]
    )

    context_close_time = (
        _candle_close_time(
            latest_context_candle,
            CONTEXT_INTERVAL_MINUTES,
        )
    )

    signal_result = analyze_market(
        signal_candles,
        symbol=symbol,
    )

    context_result = analyze_market(
        safe_context_candles,
        symbol=symbol,
    )

    signal_quality = (
        build_quality_snapshot(
            candles=signal_candles,
            strategy_result=(
                signal_result
            ),
            interval=SIGNAL_INTERVAL,
        )
    )

    context_quality = (
        build_quality_snapshot(
            candles=(
                safe_context_candles
            ),
            strategy_result=(
                context_result
            ),
            interval=CONTEXT_INTERVAL,
        )
    )

    signal_direction = (
        _signal_direction_from_result(
            signal_result
        )
    )

    context_direction = (
        _signal_direction_from_result(
            context_result
        )
    )

    signal_candidate_direction = (
        _candidate_direction_from_result(
            signal_result
        )
    )

    context_candidate_direction = (
        _candidate_direction_from_result(
            context_result
        )
    )

    alignment = (
        _direction_alignment(
            signal_direction,
            context_direction,
        )
    )

    return {
        "ready": True,

        "symbol": symbol,

        "signal_interval":
            SIGNAL_INTERVAL,

        "context_interval":
            CONTEXT_INTERVAL,

        "source_5m_count":
            source_5m_count,

        "signal_candle_count":
            signal_candle_count,

        "context_candle_count":
            context_candle_count,

        "safe_context_candle_count":
            safe_context_candle_count,

        "signal_candle_time":
            latest_signal_candle[
                "datetime"
            ],

        "signal_close_time":
            signal_close_time.strftime(
                TIME_FORMAT
            ),

        "context_candle_time":
            latest_context_candle[
                "datetime"
            ],

        "context_close_time":
            context_close_time.strftime(
                TIME_FORMAT
            ),

        "signal_direction":
            signal_direction,

        "context_direction":
            context_direction,

        "signal_candidate_direction":
            signal_candidate_direction,

        "context_candidate_direction":
            context_candidate_direction,

        "direction_alignment":
            alignment,

        "signal_result":
            signal_result,

        "context_result":
            context_result,

        "signal_quality":
            signal_quality,

        "context_quality":
            context_quality,
    }
