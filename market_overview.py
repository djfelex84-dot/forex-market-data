import os
import sqlite3

from datetime import (
    datetime,
    timezone,
)

import requests

from config import (
    SYMBOLS,
)


BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

FREE_CHANNEL_ID = os.getenv(
    "TELEGRAM_FREE_CHANNEL_ID"
)

DB_PATH = os.getenv(
    "DB_PATH",
    "/app/data/trading.db",
)


# Morning overview window:
# 07:05–09:00 UTC
#
# If the server wakes up much
# later, the morning post is skipped.
REPORT_HOUR_UTC = 7
REPORT_MINUTE_UTC = 5

LATEST_SEND_HOUR_UTC = 9

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_connection():
    connection = sqlite3.connect(
        DB_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    return connection


def init_market_overview_table():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            market_overview_log (
                report_date TEXT
                PRIMARY KEY,

                processed_at TEXT
                NOT NULL,

                status TEXT
                NOT NULL
            )
            """
        )

        connection.commit()


def overview_already_processed(
    report_date
):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT report_date

            FROM market_overview_log

            WHERE report_date = ?

            LIMIT 1
            """,
            (
                report_date,
            ),
        ).fetchone()

    return row is not None


def mark_overview_processed(
    report_date,
    status,
):
    processed_at = datetime.now(
        timezone.utc
    ).strftime(
        TIME_FORMAT
    )

    with get_connection() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO
            market_overview_log (
                report_date,
                processed_at,
                status
            )

            VALUES (?, ?, ?)
            """,
            (
                report_date,
                processed_at,
                status,
            ),
        )

        connection.commit()


def get_latest_analysis(
    symbol
):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                candle_time,
                symbol,
                interval,

                close,

                ema_fast,
                ema_slow,

                rsi,
                atr,

                ema_distance_atr,
                ema_direction,

                trend,
                signal,
                status,

                setup_score,
                blockers

            FROM market_analysis

            WHERE symbol = ?

            ORDER BY
                candle_time DESC

            LIMIT 1
            """,
            (
                symbol,
            ),
        ).fetchone()

    if row is None:
        return None

    return dict(
        row
    )


def get_trend_label(
    analysis
):
    trend = analysis[
        "trend"
    ]

    ema_direction = analysis[
        "ema_direction"
    ]

    if (
        trend == "UP"
        and ema_direction == "UP"
    ):
        return "🟢 Bullish"

    if (
        trend == "DOWN"
        and ema_direction == "DOWN"
    ):
        return "🔴 Bearish"

    return "🟡 Mixed"


def get_market_state(
    analysis
):
    status = analysis[
        "status"
    ]

    score = int(
        analysis[
            "setup_score"
        ]
        or 0
    )

    if status == "VALID":
        return "Active setup"

    if score >= 70:
        return "Watching"

    return "Waiting"


def build_market_overview(
    now
):
    lines = [
        "🌅 <b>AS · MORNING MARKET OVERVIEW</b>",
        "",
        (
            "📅 <b>"
            f"{now.strftime('%d %b %Y')}"
            "</b>"
        ),
    ]

    added_markets = 0

    for symbol in SYMBOLS:
        analysis = (
            get_latest_analysis(
                symbol
            )
        )

        if analysis is None:
            continue

        trend_label = (
            get_trend_label(
                analysis
            )
        )

        market_state = (
            get_market_state(
                analysis
            )
        )

        rsi = float(
            analysis["rsi"]
        )

        setup_score = int(
            analysis[
                "setup_score"
            ]
            or 0
        )

        lines.extend(
            [
                "",
                f"💱 <b>{symbol}</b>",
                (
                    "Trend: "
                    f"<b>{trend_label}</b>"
                ),
                (
                    "RSI: "
                    f"<b>{rsi:.1f}</b>"
                ),
                (
                    "Market state: "
                    f"<b>{market_state}</b>"
                ),
                (
                    "Setup score: "
                    f"<b>{setup_score}/100</b>"
                ),
            ]
        )

        added_markets += 1

    if added_markets == 0:
        return None

    lines.extend(
        [
            "",
            (
                "ℹ️ Full Entry / SL / TP "
                "signals are published separately."
            ),
            "",
            (
                "🕒 <b>Snapshot: "
                f"{now.strftime('%H:%M')} UTC</b>"
            ),
            "",
            "<b>AS | Forex & Crypto</b>",
            "@ASForexCrypto",
        ]
    )

    return "\n".join(
        lines
    )


def send_free_channel_message(
    text
):
    if (
        not BOT_TOKEN
        or not FREE_CHANNEL_ID
    ):
        print(
            "MARKET OVERVIEW WARNING: "
            "Telegram configuration "
            "is missing",
            flush=True,
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    try:
        response = requests.post(
            url,
            json={
                "chat_id":
                    FREE_CHANNEL_ID,

                "text":
                    text,

                "parse_mode":
                    "HTML",

                "disable_web_page_preview":
                    True,
            },
            timeout=15,
        )

        data = response.json()

        if not data.get(
            "ok"
        ):
            print(
                "MARKET OVERVIEW "
                f"TELEGRAM ERROR: {data}",
                flush=True,
            )

            return False

        return True

    except Exception as error:
        print(
            "MARKET OVERVIEW ERROR: "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

        return False


def process_market_overview():
    init_market_overview_table()

    now = datetime.now(
        timezone.utc
    )

    # Saturday / Sunday.
    if now.weekday() >= 5:
        return False

    report_date = (
        now.strftime(
            "%Y-%m-%d"
        )
    )

    if overview_already_processed(
        report_date
    ):
        return False

    due = (
        now.hour
        > REPORT_HOUR_UTC

        or (
            now.hour
            == REPORT_HOUR_UTC

            and now.minute
            >= REPORT_MINUTE_UTC
        )
    )

    if not due:
        return False

    # Too late for a useful
    # morning overview.
    if (
        now.hour
        >= LATEST_SEND_HOUR_UTC
    ):
        mark_overview_processed(
            report_date,
            "SKIPPED_LATE",
        )

        print(
            "MARKET OVERVIEW SKIPPED | "
            f"Date={report_date} | "
            "Too late",
            flush=True,
        )

        return False

    text = build_market_overview(
        now
    )

    if text is None:
        print(
            "MARKET OVERVIEW WAIT | "
            "No market data",
            flush=True,
        )

        return False

    sent = (
        send_free_channel_message(
            text
        )
    )

    if sent:
        mark_overview_processed(
            report_date,
            "SENT",
        )

        print(
            "MARKET OVERVIEW SENT | "
            f"Date={report_date}",
            flush=True,
        )

    return sent
