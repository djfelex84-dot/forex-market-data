from math import isfinite

from indicators import (
    ema,
    atr,
)


ATR_FAST_PERIOD = 14
ATR_SLOW_PERIOD = 50

EMA_FAST_PERIOD = 20
EMA_SLOW_PERIOD = 50

MOMENTUM_FAST_BARS = 3
MOMENTUM_SLOW_BARS = 6

RECENT_STRUCTURE_BARS = 6

MIN_REQUIRED_CANDLES = 60


def _to_float(
    value,
    default=0.0,
):
    if value is None:
        return default

    try:
        result = float(value)

    except (
        TypeError,
        ValueError,
    ):
        return default

    if not isfinite(result):
        return default

    return result


def _safe_ratio(
    numerator,
    denominator,
):
    numerator = _to_float(
        numerator,
        0.0,
    )

    denominator = _to_float(
        denominator,
        0.0,
    )

    if denominator == 0.0:
        return 0.0

    return (
        numerator
        / denominator
    )


def _direction_multiplier(
    direction,
):
    if direction == "BUY":
        return 1.0

    if direction == "SELL":
        return -1.0

    return 0.0


def _directional_momentum(
    closes,
    bars,
    direction,
    atr_value,
):
    if len(closes) <= bars:
        return 0.0

    movement = (
        closes[-1]
        - closes[-1 - bars]
    )

    movement *= (
        _direction_multiplier(
            direction
        )
    )

    return _safe_ratio(
        movement,
        atr_value,
    )


def _ema_value_at(
    closes,
    end_index,
    period,
):
    values = closes[
        : end_index + 1
    ]

    if len(values) < period:
        return None

    return ema(
        values,
        period,
    )


def _distance_from_range_to_value(
    low,
    high,
    value,
):
    if low <= value <= high:
        return 0.0

    if value < low:
        return (
            low
            - value
        )

    return (
        value
        - high
    )


def _recent_structure(
    candles,
    closes,
    direction,
    atr_value,
):
    start_index = max(
        0,
        len(candles)
        - RECENT_STRUCTURE_BARS,
    )

    nearest_distances = []
    directional_distances = []

    for index in range(
        start_index,
        len(candles),
    ):
        ema_fast = (
            _ema_value_at(
                closes,
                index,
                EMA_FAST_PERIOD,
            )
        )

        if ema_fast is None:
            continue

        candle = candles[index]

        low = _to_float(
            candle.get(
                "low"
            )
        )

        high = _to_float(
            candle.get(
                "high"
            )
        )

        nearest_distance = (
            _distance_from_range_to_value(
                low,
                high,
                ema_fast,
            )
        )

        nearest_distances.append(
            _safe_ratio(
                nearest_distance,
                atr_value,
            )
        )

        if direction == "BUY":
            directional_distance = (
                low
                - ema_fast
            )

        elif direction == "SELL":
            directional_distance = (
                ema_fast
                - high
            )

        else:
            continue

        directional_distances.append(
            _safe_ratio(
                directional_distance,
                atr_value,
            )
        )

    if not nearest_distances:
        nearest_ema20_distance_atr = None

    else:
        nearest_ema20_distance_atr = min(
            nearest_distances
        )

    if not directional_distances:
        min_directional_ema20_distance_atr = None
        deepest_ema20_break_atr = None

    else:
        min_directional_ema20_distance_atr = min(
            directional_distances
        )

        deepest_ema20_break_atr = max(
            0.0,
            -min_directional_ema20_distance_atr,
        )

    return {
        "nearest_ema20_distance_atr":
            nearest_ema20_distance_atr,

        "min_directional_ema20_distance_atr":
            min_directional_ema20_distance_atr,

        "deepest_ema20_break_atr":
            deepest_ema20_break_atr,
    }


def build_quality_snapshot(
    candles,
    strategy_result,
    interval="5min",
):
    if (
        len(candles)
        < MIN_REQUIRED_CANDLES
    ):
        raise ValueError(
            "Not enough candles for "
            "signal quality snapshot"
        )

    if not interval:
        raise ValueError(
            "interval is required"
        )

    direction = (
        strategy_result.get(
            "signal"
        )
    )

    if direction not in (
        "BUY",
        "SELL",
    ):
        direction = (
            strategy_result.get(
                "candidate"
            )
        )

    if direction not in (
        "BUY",
        "SELL",
    ):
        direction = "NONE"

    latest = candles[-1]
    previous = candles[-2]

    closes = [
        _to_float(
            candle.get(
                "close"
            )
        )
        for candle in candles
    ]

    ema_fast = ema(
        closes,
        EMA_FAST_PERIOD,
    )

    ema_slow = ema(
        closes,
        EMA_SLOW_PERIOD,
    )

    ema_fast_previous = ema(
        closes[:-1],
        EMA_FAST_PERIOD,
    )

    ema_slow_previous = ema(
        closes[:-1],
        EMA_SLOW_PERIOD,
    )

    atr_fast = atr(
        candles,
        ATR_FAST_PERIOD,
    )

    atr_slow = atr(
        candles,
        ATR_SLOW_PERIOD,
    )

    atr_fast = _to_float(
        atr_fast,
        0.0,
    )

    atr_slow = _to_float(
        atr_slow,
        0.0,
    )

    close = _to_float(
        latest.get(
            "close"
        )
    )

    open_price = _to_float(
        latest.get(
            "open"
        )
    )

    high = _to_float(
        latest.get(
            "high"
        )
    )

    low = _to_float(
        latest.get(
            "low"
        )
    )

    previous_close = _to_float(
        previous.get(
            "close"
        )
    )

    previous_high = _to_float(
        previous.get(
            "high"
        )
    )

    previous_low = _to_float(
        previous.get(
            "low"
        )
    )

    candle_range = max(
        high - low,
        0.0,
    )

    candle_body = abs(
        close - open_price
    )

    if candle_range > 0:
        close_location = (
            close - low
        ) / candle_range

    else:
        close_location = 0.5

    multiplier = (
        _direction_multiplier(
            direction
        )
    )

    directional_body = (
        (
            close - open_price
        )
        * multiplier
    )

    directional_change = (
        (
            close - previous_close
        )
        * multiplier
    )

    if direction == "BUY":
        directional_close_location = (
            close_location
        )

        previous_close_break = (
            close
            - previous_high
        )

    elif direction == "SELL":
        directional_close_location = (
            1.0
            - close_location
        )

        previous_close_break = (
            previous_low
            - close
        )

    else:
        directional_close_location = 0.5
        previous_close_break = 0.0

    fast_slope = (
        ema_fast
        - ema_fast_previous
    )

    slow_slope = (
        ema_slow
        - ema_slow_previous
    )

    directional_fast_slope = (
        fast_slope
        * multiplier
    )

    directional_slow_slope = (
        slow_slope
        * multiplier
    )

    directional_extension = (
        (
            close
            - ema_fast
        )
        * multiplier
    )

    ema_separation = abs(
        ema_fast
        - ema_slow
    )

    structure = (
        _recent_structure(
            candles,
            closes,
            direction,
            atr_fast,
        )
    )

    return {
        "datetime":
            latest.get(
                "datetime"
            ),

        "interval":
            interval,

        "direction":
            direction,

        "strategy_status":
            strategy_result.get(
                "status"
            ),

        "strategy_score":
            strategy_result.get(
                "setup_score"
            ),

        "rsi":
            _to_float(
                strategy_result.get(
                    "rsi"
                ),
                0.0,
            ),

        "atr_fast":
            atr_fast,

        "atr_slow":
            atr_slow,

        "atr_regime":
            _safe_ratio(
                atr_fast,
                atr_slow,
            ),

        "ema_separation_atr":
            _safe_ratio(
                ema_separation,
                atr_fast,
            ),

        "ema_fast_slope_atr":
            _safe_ratio(
                directional_fast_slope,
                atr_fast,
            ),

        "ema_slow_slope_atr":
            _safe_ratio(
                directional_slow_slope,
                atr_fast,
            ),

        "price_extension_atr":
            _safe_ratio(
                directional_extension,
                atr_fast,
            ),

        "candle_range_atr":
            _safe_ratio(
                candle_range,
                atr_fast,
            ),

        "candle_body_atr":
            _safe_ratio(
                candle_body,
                atr_fast,
            ),

        "directional_body_atr":
            _safe_ratio(
                directional_body,
                atr_fast,
            ),

        "directional_change_atr":
            _safe_ratio(
                directional_change,
                atr_fast,
            ),

        "directional_close_location":
            directional_close_location,

        "previous_close_break_atr":
            _safe_ratio(
                previous_close_break,
                atr_fast,
            ),

        "momentum_3_atr":
            _directional_momentum(
                closes,
                MOMENTUM_FAST_BARS,
                direction,
                atr_fast,
            ),

        "momentum_6_atr":
            _directional_momentum(
                closes,
                MOMENTUM_SLOW_BARS,
                direction,
                atr_fast,
            ),

        **structure,
    }
