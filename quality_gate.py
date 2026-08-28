from indicators import ema, rsi, atr
from timeframes import build_signal_timeframes


PRIMARY_TIMEFRAME = "30min"
CONTEXT_TIMEFRAME = "60min"

EMA_FAST = 20
EMA_SLOW = 50

RSI_PERIOD = 14

ATR_FAST = 14
ATR_SLOW = 50

MIN_BARS = 60

# Только сигналы с таким качеством
# смогут пройти дальше.
PASS_SCORE = 80

MIN_EMA_SEPARATION_ATR = 0.15
STRONG_EMA_SEPARATION_ATR = 0.25

# Не входим, если цена уже слишком
# далеко убежала от EMA20.
MAX_EXTENSION_ATR = 1.25

# Не торгуем слишком тихий
# или аномально резкий рынок.
MIN_ATR_REGIME = 0.55
MAX_ATR_REGIME = 1.80

PULLBACK_LOOKBACK = 4
PULLBACK_TOUCH_ATR = 0.20
PULLBACK_BREAK_ATR = 0.35


def _ema_state(candles):
    closes = [
        candle["close"]
        for candle in candles
    ]

    fast = ema(
        closes,
        EMA_FAST,
    )

    slow = ema(
        closes,
        EMA_SLOW,
    )

    fast_prev = ema(
        closes[:-1],
        EMA_FAST,
    )

    slow_prev = ema(
        closes[:-1],
        EMA_SLOW,
    )

    if fast > slow:
        trend = "BUY"

    elif fast < slow:
        trend = "SELL"

    else:
        trend = "NONE"

    if (
        fast > fast_prev
        and slow > slow_prev
    ):
        slope = "BUY"

    elif (
        fast < fast_prev
        and slow < slow_prev
    ):
        slope = "SELL"

    else:
        slope = "MIXED"

    return {
        "fast": fast,
        "slow": slow,
        "trend": trend,
        "slope": slope,
    }


def _market_state(candles):
    if len(candles) < MIN_BARS:
        raise ValueError(
            "Not enough candles "
            "for quality gate"
        )

    latest = candles[-1]
    previous = candles[-2]

    ema_state = _ema_state(
        candles
    )

    atr14 = atr(
        candles,
        ATR_FAST,
    )

    atr50 = atr(
        candles,
        ATR_SLOW,
    )

    closes = [
        candle["close"]
        for candle in candles
    ]

    rsi_value = rsi(
        closes,
        RSI_PERIOD,
    )

    if (
        atr14 <= 0
        or atr50 <= 0
    ):
        raise ValueError(
            "ATR must be positive"
        )

    separation_atr = (
        abs(
            ema_state["fast"]
            - ema_state["slow"]
        )
        / atr14
    )

    extension_atr = (
        abs(
            latest["close"]
            - ema_state["fast"]
        )
        / atr14
    )

    atr_regime = (
        atr14
        / atr50
    )

    candle_range = (
        latest["high"]
        - latest["low"]
    )

    if candle_range > 0:
        raw_close_location = (
            latest["close"]
            - latest["low"]
        ) / candle_range

    else:
        raw_close_location = 0.5

    return {
        "latest": latest,
        "previous": previous,

        "ema": ema_state,

        "atr14": atr14,
        "atr50": atr50,
        "atr_regime": atr_regime,

        "rsi": rsi_value,

        "separation_atr":
            separation_atr,

        "extension_atr":
            extension_atr,

        "raw_close_location":
            raw_close_location,
    }


def _pullback_quality(
    candles,
    state,
    direction,
):
    recent = candles[
        -(
            PULLBACK_LOOKBACK + 1
        ):
        -1
    ]

    fast = state[
        "ema"
    ]["fast"]

    slow = state[
        "ema"
    ]["slow"]

    atr14 = state[
        "atr14"
    ]

    if direction == "BUY":
        nearest = min(
            candle["low"]
            for candle in recent
        )

        touched_fast = (
            nearest
            <= fast
            + (
                PULLBACK_TOUCH_ATR
                * atr14
            )
        )

        structure_held = (
            nearest
            >= slow
            - (
                PULLBACK_BREAK_ATR
                * atr14
            )
        )

        reclaimed = (
            state[
                "latest"
            ]["close"]
            > fast
        )

    else:
        nearest = max(
            candle["high"]
            for candle in recent
        )

        touched_fast = (
            nearest
            >= fast
            - (
                PULLBACK_TOUCH_ATR
                * atr14
            )
        )

        structure_held = (
            nearest
            <= slow
            + (
                PULLBACK_BREAK_ATR
                * atr14
            )
        )

        reclaimed = (
            state[
                "latest"
            ]["close"]
            < fast
        )

    return (
        touched_fast
        and structure_held
        and reclaimed
    )


def _confirmation_score(
    state,
    direction,
):
    latest = state[
        "latest"
    ]

    previous = state[
        "previous"
    ]

    if direction == "BUY":
        directional_body = (
            latest["close"]
            > latest["open"]
        )

        continuation = (
            latest["close"]
            > previous["close"]
        )

        strong_break = (
            latest["close"]
            > previous["high"]
        )

        favorable_close = (
            state[
                "raw_close_location"
            ]
            >= 0.65
        )

    else:
        directional_body = (
            latest["close"]
            < latest["open"]
        )

        continuation = (
            latest["close"]
            < previous["close"]
        )

        strong_break = (
            latest["close"]
            < previous["low"]
        )

        favorable_close = (
            state[
                "raw_close_location"
            ]
            <= 0.35
        )

    if (
        directional_body
        and strong_break
        and favorable_close
    ):
        return 10

    if (
        directional_body
        and continuation
        and favorable_close
    ):
        return 7

    if (
        directional_body
        and continuation
    ):
        return 4

    return 0


def _rsi_score(
    rsi_value,
    direction,
):
    if direction == "BUY":

        if (
            54
            <= rsi_value
            <= 64
        ):
            return 10

        if (
            50
            <= rsi_value
            <= 68
        ):
            return 6

    else:

        if (
            36
            <= rsi_value
            <= 46
        ):
            return 10

        if (
            32
            <= rsi_value
            <= 50
        ):
            return 6

    return 0


def _empty_result(
    status,
    reason,
    blocker,
):
    return {
        "signal": "WAIT",
        "candidate": "NONE",

        "status": status,

        "quality_score": 0,
        "setup_score": 0,

        "setup_type": "NONE",

        "blockers": [
            blocker
        ],

        "reason": reason,
    }


def analyze_timeframes(
    candles_30m,
    candles_60m,
    symbol=None,
):
    if (
        len(candles_30m)
        < MIN_BARS
    ):
        return _empty_result(
            "NO_DATA",
            "Not enough 30m candles",
            "not enough 30m candles",
        )

    if (
        len(candles_60m)
        < MIN_BARS
    ):
        return _empty_result(
            "NO_DATA",
            "Not enough 60m candles",
            "not enough 60m candles",
        )

    primary = _market_state(
        candles_30m
    )

    context = _market_state(
        candles_60m
    )

    candidate = primary[
        "ema"
    ]["trend"]

    if candidate == "NONE":
        result = _empty_result(
            "NO_SETUP",
            "No clear 30m trend",
            "30m EMA trend is flat",
        )

        result.update(
            {
                "datetime":
                    primary[
                        "latest"
                    ]["datetime"],

                "close":
                    primary[
                        "latest"
                   
