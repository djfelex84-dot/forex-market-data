import time
import requests
from datetime import datetime, timezone

URL = "https://api.frankfurter.app/latest?from=EUR&to=USD"

def get_eurusd():
    response = requests.get(URL, timeout=10)
    response.raise_for_status()
    data = response.json()
    return data["rates"]["USD"]

while True:
    try:
        price = get_eurusd()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"[{now}] EUR/USD: {price}", flush=True)
    except Exception as e:
        print(f"ERROR: {e}", flush=True)

    time.sleep(60)
