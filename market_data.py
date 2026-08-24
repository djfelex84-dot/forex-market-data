import requests

from config import (
    TWELVE_DATA_API_KEY,
    SYMBOL,
    INTERVAL,
    CANDLE_LIMIT,
)

API_URL = "https://api.twelvedata.com/time_series"


def fetch_candles():
    if not TWELVE_DATA_API_KEY:
        raise RuntimeError("TWELVE_DATA_API_KEY is not set")

    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "outputsize": CANDLE_LIMIT,
        "timezone": "UTC",
        "apikey": TWELVE_DATA_API_KEY,
    }

    response = requests.get(API_URL, params=params, timeout=15)
    response.raise_for_status()

    data = response.json()

    if data.get("status") == "error":
        raise RuntimeError(data.get("message", "Twelve Data API error"))

    values = data.get("values")

    if not values:
        raise RuntimeError("No candle data received")

    # Twelve Data отдаёт новые свечи первыми.
    # Первую свечу пропускаем, потому что она может быть ещё не закрыта.
    closed_candles = values[1:]

    # Переворачиваем: от старых свечей к новым.
    closed_candles.reverse()

    candles = []

    for candle in closed_candles:
        candles.append(
            {
                "datetime": candle["datetime"],
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
            }
        )

    return candles
