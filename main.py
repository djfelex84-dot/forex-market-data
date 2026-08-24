import time
from datetime import datetime, timezone

from config import (
    CHECK_INTERVAL_SECONDS,
    SYMBOL,
    INTERVAL,
)

from market_data import fetch_candles
from strategy import analyze_market

from storage import (
    init_db,
    save_analysis,
    count_records,
)


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
        f"EMA-direction={result['ema_direction']} | "

        f"Trend={result['trend']} | "
        f"Candidate={result['candidate']} | "

        f"Signal={result['signal']} | "
        f"Status={result['status']} | "

        f"SetupScore={result['setup_score']}/100 | "

        f"{result['reason']}"
    )


def main():
    init_db()

    print(
        "Forex analysis engine started",
        flush=True,
    )

    print(
        f"Stored records: {count_records()}",
        flush=True,
    )

    while True:
        try:
            candles = fetch_candles()

            result = analyze_market(candles)

            created_at = datetime.now(
                timezone.utc
            ).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            print(
                format_result(result),
                flush=True,
            )

            saved = save_analysis(
                created_at=created_at,
                symbol=SYMBOL,
                interval=INTERVAL,
                result=result,
            )

            if saved:
                print(
                    f"New candle saved | "
                    f"Total records: {count_records()}",
                    flush=True,
                )

            else:
                print(
                    f"Candle {result['datetime']} "
                    f"already exists | skipped",
                    flush=True,
                )

        except Exception as error:
            print(
                f"ERROR: {type(error).__name__}: {error}",
                flush=True,
            )

        time.sleep(
            CHECK_INTERVAL_SECONDS
        )


if __name__ == "__main__":
    main()
