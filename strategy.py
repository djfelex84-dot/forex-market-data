from config import (
    SYMBOL,
    EMA_FAST,
    EMA_SLOW,
    RSI_PERIOD,
    ATR_PERIOD,
    MIN_EMA_DISTANCE_ATR,
    RSI_BUY_MIN,
    RSI_BUY_MAX,
    RSI_SELL_MIN,
    RSI_SELL_MAX,
    get_instrument_config,
)

from indicators import (
    ema,
    rsi,
    atr,
)


def analyze_market(
    candles,
    symbol=None,
):
    # Temporary compatibility:
    # if no symbol is supplied,
    # use the original EUR/USD symbol.
    if symbol is None:
        symbol = SYMBOL

    instrument = (
        get_instrument_config(
            symbol
        )
    )

    min_atr = instrument[
        "min_atr"
    ]

    closes = [
        candle["close"]
        for candle in candles
    ]

    ema_fast = ema(
        closes,
        EMA_FAST,
    )

    ema_slow = ema(
        closes,
        EMA_SLOW,
    )

    ema_fast_prev = ema(
        closes[:-1],
        EMA_FAST,
    )

    ema_slow_prev = ema(
        closes[:-1],
        EMA_SLOW,
    )

    ema_fast_slope = (
        ema_fast
        - ema_fast_prev
    )

    ema_slow_slope = (
        ema_slow
        - ema_slow_prev
    )

    rsi_value = rsi(
        closes,
        RSI_PERIOD,
    )

    atr_value = atr(
        candles,
        ATR_PERIOD,
    )

    latest = candles[-1]

    close = latest[
        "close"
    ]

    ema_distance = abs(
        ema_fast
        - ema_slow
    )

    if atr_value > 0:
        ema_distance_atr = (
            ema_distance
            / atr_value
        )

    else:
        ema_distance_atr = 0.0

    if ema_fast > ema_slow:
        trend = "UP"
        candidate = "BUY"

    elif ema_fast < ema_slow:
        trend = "DOWN"
        candidate = "SELL"

    else:
        trend = "FLAT"
        candidate = "NONE"

    if (
        ema_fast_slope > 0
        and ema_slow_slope > 0
    ):
        ema_direction = "UP"

    elif (
        ema_fast_slope < 0
        and ema_slow_slope < 0
    ):
        ema_direction = "DOWN"

    else:
        ema_direction = "MIXED"

    if candidate == "BUY":
        checks = {
            "volatility":
                atr_value
                >= min_atr,

            "ema_separation":
                ema_distance_atr
                >= MIN_EMA_DISTANCE_ATR,

            "ema_slope":
                ema_direction
                == "UP",

            "price_position":
                close > ema_fast
                and close > ema_slow,

            "rsi":
                RSI_BUY_MIN
                <= rsi_value
                <= RSI_BUY_MAX,
        }

        blocker_messages = {
            "volatility":
                "volatility too low",

            "ema_separation":
                "weak EMA separation",

            "ema_slope":
                (
                    "EMAs are not "
                    "rising together"
                ),

            "price_position":
                (
                    "price is not "
                    "above both EMAs"
                ),

            "rsi":
                (
                    "RSI does not "
                    "confirm BUY"
                ),
        }

    elif candidate == "SELL":
        checks = {
            "volatility":
                atr_value
                >= min_atr,

            "ema_separation":
                ema_distance_atr
                >= MIN_EMA_DISTANCE_ATR,

            "ema_slope":
                ema_direction
                == "DOWN",

            "price_position":
                close < ema_fast
                and close < ema_slow,

            "rsi":
                RSI_SELL_MIN
                <= rsi_value
                <= RSI_SELL_MAX,
        }

        blocker_messages = {
            "volatility":
                "volatility too low",

            "ema_separation":
                "weak EMA separation",

            "ema_slope":
                (
                    "EMAs are not "
                    "falling together"
                ),

            "price_position":
                (
                    "price is not "
                    "below both EMAs"
                ),

            "rsi":
                (
                    "RSI does not "
                    "confirm SELL"
                ),
        }

    else:
        checks = {}
        blocker_messages = {}

    weights = {
        "volatility": 10,
        "ema_separation": 20,
        "ema_slope": 20,
        "price_position": 20,
        "rsi": 30,
    }

    if candidate == "NONE":
        setup_score = 0
        signal = "WAIT"
        status = "NO_SETUP"

        blockers = [
            "no clear EMA trend"
        ]

    else:
        setup_score = sum(
            weights[name]
            for name, passed
            in checks.items()
            if passed
        )

        blockers = [
            blocker_messages[name]
            for name, passed
            in checks.items()
            if not passed
        ]

        if all(
            checks.values()
        ):
            signal = candidate
            status = "VALID"

        else:
            signal = "WAIT"
            status = "BLOCKED"

    if blockers:
        reason = (
            "Blocked by: "
            + "; ".join(
                blockers
            )
        )

    else:
        reason = (
            "All strategy "
            "filters passed"
        )

    return {
        "datetime":
            latest["datetime"],

        "close":
            close,

        "ema_fast":
            ema_fast,

        "ema_slow":
            ema_slow,

        "ema_fast_slope":
            ema_fast_slope,

        "ema_slow_slope":
            ema_slow_slope,

        "ema_direction":
            ema_direction,

        "ema_distance_atr":
            ema_distance_atr,

        "rsi":
            rsi_value,

        "atr":
            atr_value,

        "trend":
            trend,

        "candidate":
            candidate,

        "signal":
            signal,

        "status":
            status,

        "setup_score":
            setup_score,

        "blockers":
            blockers,

        "reason":
            reason,
    }
