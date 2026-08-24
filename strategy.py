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

    ema_fast = ema(closes, EMA_FAST)
    ema_slow = ema(closes, EMA_SLOW)
    rsi_value = rsi(closes, RSI_PERIOD)
    atr_value = atr(candles, ATR_PERIOD)

    latest = candles[-1]
    close = latest["close"]

    ema_distance = abs(ema_fast - ema_slow)

    if atr_value > 0:
        ema_distance_atr = ema_distance / atr_value
    else:
        ema_distance_atr = 0

    # Trend
    if ema_fast > ema_slow:
        trend = "UP"
    elif ema_fast < ema_slow:
        trend = "DOWN"
    else:
        trend = "FLAT"

    signal = "WAIT"
    reasons = []
    score = 0

    # -------------------------
    # Market quality
    # -------------------------

    if atr_value >= MIN_ATR:
        score += 15
        reasons.append("volatility OK")
    else:
        reasons.append("volatility too low")

    if ema_distance_atr >= MIN_EMA_DISTANCE_ATR:
        score += 20
        reasons.append("trend separation OK")
    else:
        reasons.append("weak EMA separation")

    # -------------------------
    # BUY analysis
    # -------------------------

    if trend == "UP":
        score += 25

        if RSI_BUY_MIN <= rsi_value <= RSI_BUY_MAX:
            score += 25

            if (
                atr_value >= MIN_ATR
                and ema_distance_atr >= MIN_EMA_DISTANCE_ATR
            ):
                signal = "BUY"
                reasons.append("bullish EMA structure")
                reasons.append("RSI confirms bullish momentum")
        else:
            reasons.append("RSI does not confirm BUY")

    # -------------------------
    # SELL analysis
    # -------------------------

    elif trend == "DOWN":
        score += 25

        if RSI_SELL_MIN <= rsi_value <= RSI_SELL_MAX:
            score += 25

            if (
                atr_value >= MIN_ATR
                and ema_distance_atr >= MIN_EMA_DISTANCE_ATR
            ):
                signal = "SELL"
                reasons.append("bearish EMA structure")
                reasons.append("RSI confirms bearish momentum")
        else:
            reasons.append("RSI does not confirm SELL")

    else:
        reasons.append("no clear trend")

    # Price position adds confirmation
    if signal == "BUY" and close > ema_fast:
        score += 15
        reasons.append("price above fast EMA")

    elif signal == "SELL" and close < ema_fast:
        score += 15
        reasons.append("price below fast EMA")

    # Cap score
    confidence = min(score, 100)

    return {
        "datetime": latest["datetime"],
        "close": close,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "ema_distance_atr": ema_distance_atr,
        "rsi": rsi_value,
        "atr": atr_value,
        "trend": trend,
        "signal": signal,
        "confidence": confidence,
        "reason": "; ".join(reasons),
    }
