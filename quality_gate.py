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

# Research candidate threshold. This module is not wired into live decisions.
PASS_SCORE = 80

MIN_EMA_SEPARATION_ATR = 0.15
STRONG_EMA_SEPARATION_ATR = 0.25

# Avoid entries after price has already moved too far away from EMA20.
MAX_EXTENSION_ATR = 1.25
GOOD_EXTENSION_ATR = 0.75

# Avoid abnormally quiet or explosive volatility regimes.
MIN_ATR_REGIME = 0.55
MAX_ATR_REGIME = 1.80
GOOD_ATR_REGIME_MIN = 0.75
GOOD_ATR_REGIME_MAX = 1.40

PULLBACK_LOOKBACK = 4
PULLBACK_TOUCH_ATR = 0.20
PULLBACK_BREAK_ATR = 0.35

SETUP_TYPE = "M30_H1_PULLBACK_RECLAIM"


def _ema_state(candles):
    closes = [candle["close"] for candle in candles]

    fast = ema(closes, EMA_FAST)
    slow = ema(closes, EMA_SLOW)
    fast_prev = ema(closes[:-1], EMA_FAST)
    slow_prev = ema(closes[:-1], EMA_SLOW)

    if fast > slow:
        trend = "BUY"
    elif fast < slow:
        trend = "SELL"
    else:
        trend = "NONE"

    if fast > fast_prev and slow > slow_prev:
        slope = "BUY"
    elif fast < fast_prev and slow < slow_prev:
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
        raise ValueError("Not enough candles for quality gate")

    latest = candles[-1]
    previous = candles[-2]
    ema_state = _ema_state(candles)

    atr14 = atr(candles, ATR_FAST)
    atr50 = atr(candles, ATR_SLOW)
    closes = [candle["close"] for candle in candles]
    rsi_value = rsi(closes, RSI_PERIOD)

    if atr14 <= 0 or atr50 <= 0:
        raise ValueError("ATR must be positive")

    separation_atr = abs(ema_state["fast"] - ema_state["slow"]) / atr14
    extension_atr = abs(latest["close"] - ema_state["fast"]) / atr14
    atr_regime = atr14 / atr50

    candle_range = latest["high"] - latest["low"]
    if candle_range > 0:
        raw_close_location = (latest["close"] - latest["low"]) / candle_range
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
        "separation_atr": separation_atr,
        "extension_atr": extension_atr,
        "raw_close_location": raw_close_location,
    }


def _pullback_quality(candles, state, direction):
    recent = candles[-(PULLBACK_LOOKBACK + 1):-1]

    fast = state["ema"]["fast"]
    slow = state["ema"]["slow"]
    atr14 = state["atr14"]

    if direction == "BUY":
        nearest = min(candle["low"] for candle in recent)
        touched_fast = nearest <= fast + (PULLBACK_TOUCH_ATR * atr14)
        structure_held = nearest >= slow - (PULLBACK_BREAK_ATR * atr14)
        reclaimed = state["latest"]["close"] > fast
    else:
        nearest = max(candle["high"] for candle in recent)
        touched_fast = nearest >= fast - (PULLBACK_TOUCH_ATR * atr14)
        structure_held = nearest <= slow + (PULLBACK_BREAK_ATR * atr14)
        reclaimed = state["latest"]["close"] < fast

    return touched_fast and structure_held and reclaimed


def _confirmation_score(state, direction):
    latest = state["latest"]
    previous = state["previous"]

    if direction == "BUY":
        directional_body = latest["close"] > latest["open"]
        continuation = latest["close"] > previous["close"]
        strong_break = latest["close"] > previous["high"]
        favorable_close = state["raw_close_location"] >= 0.65
    else:
        directional_body = latest["close"] < latest["open"]
        continuation = latest["close"] < previous["close"]
        strong_break = latest["close"] < previous["low"]
        favorable_close = state["raw_close_location"] <= 0.35

    if directional_body and strong_break and favorable_close:
        return 10
    if directional_body and continuation and favorable_close:
        return 7
    if directional_body and continuation:
        return 4
    return 0


def _rsi_score(rsi_value, direction):
    if direction == "BUY":
        if 54 <= rsi_value <= 64:
            return 10
        if 50 <= rsi_value <= 68:
            return 6
    else:
        if 36 <= rsi_value <= 46:
            return 10
        if 32 <= rsi_value <= 50:
            return 6
    return 0


def _empty_result(status, reason, blocker):
    return {
        "signal": "WAIT",
        "candidate": "NONE",
        "status": status,
        "quality_score": 0,
        "setup_score": 0,
        "setup_type": "NONE",
        "blockers": [blocker],
        "reason": reason,
    }


def _with_context(result, primary, context, candidate):
    result.update(
        {
            "datetime": primary["latest"]["datetime"],
            "close": primary["latest"]["close"],
            "candidate": candidate,
            "primary_trend": primary["ema"]["trend"],
            "primary_slope": primary["ema"]["slope"],
            "context_trend": context["ema"]["trend"],
            "context_slope": context["ema"]["slope"],
            "primary_rsi": primary["rsi"],
            "context_rsi": context["rsi"],
            "primary_atr": primary["atr14"],
            "primary_atr_regime": primary["atr_regime"],
            "primary_ema_separation_atr": primary["separation_atr"],
            "primary_extension_atr": primary["extension_atr"],
        }
    )
    return result


def _reject(primary, context, candidate, blocker, reason):
    result = _empty_result("REJECTED", reason, blocker)
    result["setup_type"] = SETUP_TYPE
    return _with_context(result, primary, context, candidate)


def analyze_timeframes(candles_30m, candles_60m, symbol=None):
    """Evaluate one closed M30 setup using only already-closed H1 context.

    The function is intentionally self-contained and research-safe: it returns a
    decision object and performs no storage, Telegram, broker, or production IO.
    """

    if len(candles_30m) < MIN_BARS:
        return _empty_result(
            "NO_DATA",
            "Not enough 30m candles",
            "not enough 30m candles",
        )

    if len(candles_60m) < MIN_BARS:
        return _empty_result(
            "NO_DATA",
            "Not enough 60m candles",
            "not enough 60m candles",
        )

    primary = _market_state(candles_30m)
    context = _market_state(candles_60m)
    candidate = primary["ema"]["trend"]

    if candidate == "NONE":
        return _reject(
            primary,
            context,
            candidate,
            "30m EMA trend is flat",
            "No clear 30m trend",
        )

    opposite = "SELL" if candidate == "BUY" else "BUY"

    if context["ema"]["trend"] != candidate:
        return _reject(
            primary,
            context,
            candidate,
            "60m trend conflict",
            "30m direction is not confirmed by closed 60m context",
        )

    if primary["ema"]["slope"] != candidate:
        return _reject(
            primary,
            context,
            candidate,
            "30m EMA slope not aligned",
            "30m EMA20/EMA50 slope does not support the candidate",
        )

    if context["ema"]["slope"] == opposite:
        return _reject(
            primary,
            context,
            candidate,
            "60m EMA slope conflict",
            "60m EMA slope is moving against the candidate",
        )

    if primary["separation_atr"] < MIN_EMA_SEPARATION_ATR:
        return _reject(
            primary,
            context,
            candidate,
            "weak EMA separation",
            "30m EMA separation is too small relative to ATR",
        )

    if primary["extension_atr"] > MAX_EXTENSION_ATR:
        return _reject(
            primary,
            context,
            candidate,
            "price overextended",
            "Price is too far from 30m EMA20",
        )

    if not (MIN_ATR_REGIME <= primary["atr_regime"] <= MAX_ATR_REGIME):
        return _reject(
            primary,
            context,
            candidate,
            "unfavorable ATR regime",
            "30m volatility regime is outside the allowed range",
        )

    if not _pullback_quality(candles_30m, primary, candidate):
        return _reject(
            primary,
            context,
            candidate,
            "no valid pullback/reclaim",
            "Recent price action did not produce a clean pullback and reclaim",
        )

    confirmation = _confirmation_score(primary, candidate)
    rsi_raw = _rsi_score(primary["rsi"], candidate)

    components = {
        "context_trend": 15,
        "primary_slope": 10,
        "context_slope": 10 if context["ema"]["slope"] == candidate else 5,
        "ema_separation": 10 if primary["separation_atr"] >= STRONG_EMA_SEPARATION_ATR else 6,
        "extension": 10 if primary["extension_atr"] <= GOOD_EXTENSION_ATR else 6,
        "atr_regime": (
            10
            if GOOD_ATR_REGIME_MIN <= primary["atr_regime"] <= GOOD_ATR_REGIME_MAX
            else 6
        ),
        "pullback_reclaim": 20,
        "confirmation": confirmation,
        "rsi": 5 if rsi_raw == 10 else (3 if rsi_raw == 6 else 0),
    }

    quality_score = sum(components.values())
    passed = quality_score >= PASS_SCORE

    result = {
        "signal": candidate if passed else "WAIT",
        "candidate": candidate,
        "status": "VALID" if passed else "BELOW_SCORE",
        "quality_score": quality_score,
        "setup_score": quality_score,
        "setup_type": SETUP_TYPE,
        "blockers": [] if passed else [f"quality score below {PASS_SCORE}"],
        "reason": (
            "M30/H1 pullback-reclaim setup passed quality gate"
            if passed
            else f"Setup structurally valid but quality score {quality_score}/{PASS_SCORE} is too low"
        ),
        "score_components": components,
        "confirmation_score": confirmation,
        "rsi_score_raw": rsi_raw,
        "symbol": symbol,
    }

    return _with_context(result, primary, context, candidate)


def analyze_five_minute_candles(five_minute_candles, symbol=None):
    """Convenience wrapper for live/shadow research using closed 5m candles."""

    timeframes = build_signal_timeframes(five_minute_candles)
    candles_30m = timeframes[PRIMARY_TIMEFRAME]
    candles_60m = timeframes[CONTEXT_TIMEFRAME]

    return analyze_timeframes(
        candles_30m=candles_30m,
        candles_60m=candles_60m,
        symbol=symbol,
    )
