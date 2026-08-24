import time
from datetime import datetime, timezone

from config import (
    SYMBOL,
    INTERVAL,
)

from market_data import fetch_candles
from strategy import analyze_market

from storage import (
    init_db,
    save_analysis,
    count_records,
    get_outcome_summary,
)

from evaluator import evaluate_pending_signals


# Небольшая задержка после закрытия свечи,
# чтобы источник данных успел обновиться.
CANDLE_CLOSE_DELAY_SECONDS = 15


def interval_to_seconds(interval):
    if interval.endswith("min"):
        minutes = int(interval.replace("min", ""))
        return minutes * 60

    if interval.endswith("h"):
        hours = int(interval.replace("h", ""))
        return hours * 3600

    raise ValueError(
        f"Unsupported interval: {interval}"
    )


INTERVAL_SECONDS = interval_to_seconds(
    INTERVAL
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


def print_outcome_summary():
    summary = get_outcome_summary()

    if not summary:
        return

    print(
        "----- SIGNAL STATISTICS -----",
        flush=True,
    )

    for row in summary:
        total = row["total"]
        wins = row["wins"] or 0

        win_rate = (
            wins / total * 100
            if total
            else 0
        )

        avg_pips = row["avg_pips"] or 0

        print(
            f"{row['horizon_minutes']}m | "
            f"Signals={total} | "
            f"Wins={wins} | "
            f"Losses={row['losses'] or 0} | "
            f"Flat={row['flat'] or 0} | "
            f"WinRate={win_rate:.1f}% | "
            f"AvgPips={avg_pips:.2f}",
            flush=True,
        )


def analyze_once():
    # Один API-запрос на цикл
    candles = fetch_candles()

    result = analyze_market(
        candles
    )

    created_at = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    print(
        format_result(result),
        flush=True,
    )

    saved, analysis_id = save_analysis(
        created_at=created_at,
        symbol=SYMBOL,
        interval=INTERVAL,
        result=result,
    )

    if saved:
        print(
            f"New candle saved | "
            f"Total records: "
            f"{count_records()}",
            flush=True,
        )
    else:
        print(
            f"Candle "
            f"{result['datetime']} "
            f"already exists | skipped",
            flush=True,
        )

    # Проверяем старые VALID BUY/SELL
    # по тем же свечам — новых API-запросов нет.
    outcomes = evaluate_pending_signals(
        candles
    )

    if outcomes:
        for outcome in outcomes:
            print(
                f"OUTCOME | "
                f"{outcome['signal']} | "
                f"Signal candle="
                f"{outcome['signal_time']} | "
                f"After="
                f"{outcome['horizon']}m | "
                f"{outcome['result']} | "
                f"Pips="
                f"{outcome['pips']:.2f} | "
                f"SetupScore="
                f"{outcome['score']}/100",
                flush=True,
            )

        print_outcome_summary()


def seconds_until_next_check():
    now = time.time()

    next_boundary = (
        (int(now) // INTERVAL_SECONDS + 1)
        * INTERVAL_SECONDS
    )

    target = (
        next_boundary
        + CANDLE_CLOSE_DELAY_SECONDS
    )

    return max(
        target - now,
        1,
    )


def print_next_check(wait_seconds):
    next_check = datetime.fromtimestamp(
        time.time() + wait_seconds,
        tz=timezone.utc,
    )

    print(
        f"Next market check: "
        f"{next_check.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        flush=True,
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

    print(
        f"Schedule: every {INTERVAL}, "
        f"{CANDLE_CLOSE_DELAY_SECONDS}s "
        f"after candle boundary",
        flush=True,
    )

    # Один анализ сразу после запуска.
    # Если эта свеча уже есть, защита от дублей
    # просто не даст сохранить её второй раз.
    try:
        analyze_once()

    except Exception as error:
        print(
            f"ERROR: "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

    while True:
        wait_seconds = (
            seconds_until_next_check()
        )

        print_next_check(
            wait_seconds
        )

        time.sleep(
            wait_seconds
        )

        try:
            analyze_once()

        except Exception as error:
            print(
                f"ERROR: "
                f"{type(error).__name__}: "
                f"{error}",
                flush=True,
            )


if __name__ == "__main__":
    main()
