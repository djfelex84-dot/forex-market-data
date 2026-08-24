import os
import time
import requests
from datetime import datetime, timezone

API_KEY = os.getenv("TWELVE_DATA_API_KEY")

URL = "https://api.twelvedata.com/time_series"

def get_candles():
    params = {
        "symbol": "EUR/USD",
        "interval": "5min",
        "outputsize": 5,
        "apikey": API_KEY,
    }

    response = requests.get(URL, params=params, timeout=15)
    response.raise_for_status()

    data = response.json()

    if data.get("status") == "error":
        raise RuntimeError(data.get("message", "Twelve Data API error"))

    return data["values"]

while True:
    try:
        candles = get_candles()
        latest = candles[0]

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        print(
            f"[{now}] EUR/USD 5m | "
            f"time={latest['datetime']} "
            f"O={latest['open']} "
            f"H={latest['high']} "
            f"L={latest['low']} "
            f"C={latest['close']}",
            flush=True,
        )

    except Exception as e:
        print(f"ERROR: {e}", flush=True)

    time.sleep(300)
