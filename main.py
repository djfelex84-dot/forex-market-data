import time
from datetime import datetime, timezone

from config import CHECK_INTERVAL_SECONDS, SYMBOL, INTERVAL
from market_data import fetch_candles
from strategy import analyze_market


def format_result(result):
    return (
        f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] "
        f"{SYMBOL} {INTERVAL} | "
        f"Candle={result['datetime']} | "
        f"Close={result['close']:.5f} | "
        f"EMA20={result['ema_fast']:.5f} | "
        f"EMA50={result['ema_slow']:.5f} | "
        f"RSI14={result['rsi']:.2f} | "
        f"ATR14={result['atr']:.5f} | "
        f"EMA-distance={result['ema_distance_atr']:.2f} ATR | "
        f"Trend={result['trend']} | "
        f"Signal={result['signal']} | "
        f"Confidence={result['confidence']}% | "
        f"Reason={result['reason']}"
    )


def main():
    print("Forex analysis engine started", flush=True)

    while True:
        try:
            candles = fetch_candles()
            result = analyze_market(candles)

            print(format_result(result), flush=True)

        except Exception as error:
            print(f"ERROR: {error}", flush=True)

        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
