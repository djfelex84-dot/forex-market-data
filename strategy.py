from config import (
    EMA_FAST,
    EMA_SLOW,
    RSI_PERIOD,
    ATR_PERIOD,
    MIN_EMA_DISTANCE_ATR,
    MIN_ATR,
    RSI_BUY_MIN,
    RSI_BUY_MAX,
    RSI_SELL_MIN,
    RSI_SELL_MAX,
)

from indicators import ema, rsi, atr


def analyze_market(candles):
    closes = [candle["close"] for candle in candles]

    # Current indicators
    ema_fast = ema(closes, EMA_FAST)
    ema_slow = ema(closes, EMA_SLOW)
    rsi_value = rsi(closes, RSI_PERIOD)
    atr_value = atr(candles, ATR_PERIOD)

    # Previous EMA values to determine slope
    ema_fast_prev = ema(closes[:-1], EMA_FAST)
    ema_slow_prev = ema(closes[:-1], EMA_SLOW)

    ema_fast_slope = ema_fast - ema_fast_prev
    ema_slow_slope = ema_slow - ema_slow_prev

    latest = candles[-1]
    close = latest["close"]

    ema_distance = abs(ema_fast - ema_slow)

    if atr_value > 0:
        ema_distance_atr = ema_distance / atr_value
    else:
        ema_distance_atr = 0

    # Main trend
    if ema_fast > ema_slow:
        trend = "UP"
    elif ema_fast < ema_slow:
        trend = "DOWN"
    else:
        trend = "FLAT"

    # EMA direction
    if ema_fast_slope > 0 and ema_slow_slope > 0:
        ema_direction = "UP"
    elif ema_fast_slope < 0 and ema_slow_slope < 0:
        ema_direction = "DOWN"
    else:
        ema_direction = "MIXED"

    signal = "WAIT"
    reasons = []
    score = 0

    # -------------------------
    # Volatility
    # -------------------------

    if atr_value >= MIN_ATR:
        score += 10
        reasons.append("volatility OK")
    else:
        reasons.append("volatility too low")

    # -------------------------
    # EMA separation
    # -------------------------

    if ema_distance_atr >= MIN_EMA_DISTANCE_ATR:
        score += 15
        reasons.append("EMA separation OK")
    else:
        reasons.append("weak EMA separation")

    # -------------------------
    # BUY analysis
    # -------------------------

    if trend == "UP":
        score += 20

        if ema_direction == "UP":
            score += 15
            reasons.append("EMAs rising")
        else:
            reasons.append("EMA slope not bullish")

        if close > ema_fast and close > ema_slow:
            score += 15
            reasons.append("price above both EMAs")
        else:
            reasons.append("price not above both EMAs")

        if RSI_BUY_MIN <= rsi_value <= RSI_BUY_MAX:
            score += 25
            reasons.append("RSI confirms BUY")
        else:
            reasons.append("RSI does not confirm BUY")

        if (
            atr_value >= MIN_ATR
            and ema_distance_atr >= MIN_EMA_DISTANCE_ATR
            and ema_direction == "UP"
            and close > ema_fast
            and close > ema_slow
            and RSI_BUY_MIN <= rsi_value <= RSI_BUY_MAX
        ):
            signal = "BUY"

    # -------------------------
    # SELL analysis
    # -------------------------

    elif trend == "DOWN":
        score += 20

        if ema_direction == "DOWN":
            score += 15
            reasons.append("EMAs falling")
        else:
            reasons.append("EMA slope not bearish")

        if close < ema_fast and close < ema_slow:
            score += 15
            reasons.append("price below both EMAs")
        else:
            reasons.append("price not below both EMAs")

        if RSI_SELL_MIN <= rsi_value <= RSI_SELL_MAX:
            score += 25
            reasons.append("RSI confirms SELL")
        else:
            reasons.append("RSI does not confirm SELL")

        if (
            atr_value >= MIN_ATR
            and ema_distance_atr >= MIN_EMA_DISTANCE_ATR
            and ema_direction == "DOWN"
            and close < ema_fast
            and close < ema_slow
            and RSI_SELL_MIN <= rsi_value <= RSI_SELL_MAX
        ):
            signal = "SELL"

    else:
        reasons.append("no clear trend")

    confidence = min(score, 100)

    return {
        "datetime": latest["datetime"],
        "close": close,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "ema_fast_slope": ema_fast_slope,
        "ema_slow_slope": ema_slow_slope,
        "ema_direction": ema_direction,
        "ema_distance_atr": ema_distance_atr,
        "rsi": rsi_value,
        "atr": atr_value,
        "trend": trend,
        "signal": signal,
        "confidence": confidence,
        "reason": "; ".join(reasons),
    }
