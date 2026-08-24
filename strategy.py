from config import (
    EMA_FAST,
    EMA_SLOW,
    RSI_PERIOD,
    ATR_PERIOD,
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

    if ema_fast > ema_slow:
        trend = "UP"
    elif ema_fast < ema_slow:
        trend = "DOWN"
    else:
        trend = "FLAT"

    signal = "WAIT"
    reasons = []

    # BUY logic
    if (
        ema_fast > ema_slow
        and 50 <= rsi_value <= 70
    ):
        signal = "BUY"
        reasons.append("EMA fast is above EMA slow")
        reasons.append("RSI confirms bullish momentum")

    # SELL logic
    elif (
        ema_fast < ema_slow
        and 30 <= rsi_value <= 50
    ):
        signal = "SELL"
        reasons.append("EMA fast is below EMA slow")
        reasons.append("RSI confirms bearish momentum")

    else:
        reasons.append("No valid setup")

    return {
        "datetime": latest["datetime"],
        "close": close,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "rsi": rsi_value,
        "atr": atr_value,
        "trend": trend,
        "signal": signal,
        "reason": "; ".join(reasons),
    }
