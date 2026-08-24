import requests
from datetime import datetime, timedelta, timezone

from config import (
    TWELVE_DATA_API_KEY,
    SYMBOL,
    INTERVAL,
    CANDLE_LIMIT,
)

API_URL = "https://api.twelvedata.com/time_series"

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def interval_to_timedelta(interval):
    if interval.endswith("min"):
        minutes = int(interval.replace("min", ""))
        return timedelta(minutes=minutes)

    if interval.endswith("h"):
        hours = int(interval.replace("h", ""))
        return timedelta(hours=hours)

    raise ValueError(
        f"Unsupported interval: {interval}"
    )


INTERVAL_DELTA = interval_to_timedelta(
    INTERVAL
)


def fetch_candles():
    if not TWELVE_DATA_API_KEY:
        raise RuntimeError(
            "TWELVE_DATA_API_KEY is not set"
        )

    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "outputsize": CANDLE_LIMIT,
        "timezone": "UTC",
        "apikey": TWELVE_DATA_API_KEY,
    }

    response = requests.get(
        API_URL,
        params=params,
        timeout=15,
    )

    response.raise_for_status()

    data = response.json()

    if data.get("status") == "error":
        raise RuntimeError(
            data.get(
                "message",
                "Twelve Data API error",
            )
        )

    values = data.get("values")

    if not values:
        raise RuntimeError(
            "No candle data received"
        )

    now = datetime.now(timezone.utc)

    candles = []

    for candle in values:
        candle_open = datetime.strptime(
            candle["datetime"],
            TIME_FORMAT,
        ).replace(
            tzinfo=timezone.utc
        )

        candle_close = (
            candle_open + INTERVAL_DELTA
        )

        # Используем только реально
        # закрытые свечи.
        if candle_close > now:
            continue

        candles.append(
            {
                "datetime": candle["datetime"],
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
            }
        )

    if not candles:
        raise RuntimeError(
            "No closed candles available"
        )

    # Twelve Data отдаёт сначала новые.
    # Нам для индикаторов нужны:
    # старые -> новые.
    candles.reverse()

    return candles
