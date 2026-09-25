import time
from datetime import (
    datetime,
    timezone,
)

from config import (
    CRYPTO_ASSETS,
    CRYPTO_ENTRY_DAYS,
    CRYPTO_EXIT_DAYS,
)

from crypto_signals import (
    init_crypto_tables,
    process_crypto_signals,
)

from economic_calendar import (
    init_economic_calendar,
    process_economic_calendar,
)

from news_digest import (
    init_news_digest_tables,
    process_news_digest,
)

from user_subscriptions import (
    init_user_subscription_tables,
)

from telegram_user_bot import (
    start_user_bot_polling,
)


# The forex M5 signal engine
# (strategy.py, trade_manager.py,
# daily/weekly forex reports,
# market overview, health monitor)
# was switched off on 2026-09-25:
# it lost money on independent
# 2016-2020 data, see
# BOT_STRATEGY_AUDIT_RESULT.md.
# The modules stay in the repo
# for reference only.

CHECK_INTERVAL_SECONDS = 300


def run_step(
    name,
    function,
):
    try:
        function()

    except Exception as error:
        print(
            f"{name} ERROR | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )


def run_once():
    run_step(
        "CRYPTO SIGNALS",
        process_crypto_signals,
    )

    run_step(
        "CALENDAR",
        process_economic_calendar,
    )

    run_step(
        "NEWS DIGEST",
        process_news_digest,
    )


def seconds_until_next_check():
    now = time.time()

    next_boundary = (
        (
            int(now)
            // CHECK_INTERVAL_SECONDS
            + 1
        )
        * CHECK_INTERVAL_SECONDS
    )

    return max(
        next_boundary - now,
        1,
    )


def main():
    init_crypto_tables()
    init_economic_calendar()
    init_news_digest_tables()
    init_user_subscription_tables()

    print(
        "AS crypto signal engine started",
        flush=True,
    )

    print(
        "Crypto assets: "
        + ", ".join(
            CRYPTO_ASSETS
        ),
        flush=True,
    )

    print(
        "Strategy: daily close above "
        f"{CRYPTO_ENTRY_DAYS}-day high -> BUY, "
        f"below {CRYPTO_EXIT_DAYS}-day low -> EXIT "
        "| checked once a day after 00:10 UTC",
        flush=True,
    )

    print(
        "VIP channel: entry/exit signals + daily trend board",
        flush=True,
    )

    print(
        "Free channel: closed results, weekly track record, "
        "economic calendar, news digest",
        flush=True,
    )

    try:
        start_user_bot_polling()

    except Exception as error:
        print(
            "USER BOT START ERROR | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

    run_once()

    while True:
        wait_seconds = (
            seconds_until_next_check()
        )

        next_check = (
            datetime.fromtimestamp(
                time.time()
                + wait_seconds,
                tz=timezone.utc,
            )
        )

        print(
            f"Next check: "
            f"{next_check.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            flush=True,
        )

        time.sleep(
            wait_seconds
        )

        run_once()


if __name__ == "__main__":
    main()
