import os
import sqlite3
import time
from datetime import datetime, timezone

import requests


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

CALENDAR_URL = (
    "https://nfs.faireconomy.media/"
    "ff_calendar_thisweek.json"
)

MAJOR_CURRENCIES = {
    "USD",
    "EUR",
    "GBP",
    "JPY",
    "CHF",
    "CAD",
    "AUD",
    "NZD",
}

CACHE_SECONDS = 1800

WARNING_MIN_MINUTES = 25
WARNING_MAX_MINUTES = 35


_calendar_cache = None
_calendar_cache_time = 0


def get_connection():
    connection = sqlite3.connect(
        DB_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    return connection


def init_economic_calendar():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            economic_calendar_log (
                event_key TEXT NOT NULL,
                notification_type TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY (
                    event_key,
                    notification_type
                )
            )
            """
        )

        connection.commit()


def fetch_calendar():
    global _calendar_cache
    global _calendar_cache_time

    now_timestamp = time.time()

    if (
        _calendar_cache is not None
        and (
            now_timestamp
            - _calendar_cache_time
        ) < CACHE_SECONDS
    ):
        return _calendar_cache

    response = requests.get(
        CALENDAR_URL,
        timeout=20,
        headers={
            "User-Agent": (
                "AS-Forex-Crypto/"
                "1.0"
            )
        },
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(
        data,
        list,
    ):
        raise RuntimeError(
            "Economic calendar "
            "returned invalid data"
        )

    _calendar_cache = data
    _calendar_cache_time = (
        now_timestamp
    )

    return data


def parse_event_time(
    event
):
    raw_date = event.get(
        "date",
        "",
    )

    if not raw_date:
        return None

    try:
        event_time = (
            datetime.fromisoformat(
                raw_date
            )
        )

        return event_time.astimezone(
            timezone.utc
        )

    except ValueError:
        return None


def event_key(
    event,
    event_time,
):
    return (
        f"{event.get('country', '')}|"
        f"{event.get('title', '')}|"
        f"{event_time.isoformat()}"
    )


def notification_sent(
    key,
    notification_type,
):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM economic_calendar_log
            WHERE event_key = ?
              AND notification_type = ?
            LIMIT 1
            """,
            (
                key,
                notification_type,
            ),
        ).fetchone()

    return row is not None


def mark_notification_sent(
    key,
    notification_type,
):
    sent_at = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    with get_connection() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO
            economic_calendar_log (
                event_key,
                notification_type,
                sent_at
            )
            VALUES (?, ?, ?)
            """,
            (
                key,
                notification_type,
                sent_at,
            ),
        )

        connection.commit()


def send_free_message(
    text
):
    if (
        not BOT_TOKEN
        or not FREE_CHANNEL_ID
    ):
        print(
            "CALENDAR WARNING: "
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

        if not data.get("ok"):
            print(
                "CALENDAR TELEGRAM ERROR: "
                f"{data}",
                flush=True,
            )

            return False

        return True

    except Exception as error:
        print(
            "CALENDAR TELEGRAM ERROR: "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

        return False


def clean_value(
    value
):
    if value is None:
        return "—"

    value = str(
        value
    ).strip()

    if not value:
        return "—"

    return value


def get_high_impact_events():
    calendar = fetch_calendar()

    events = []

    for event in calendar:
        country = event.get(
            "country",
            "",
        )

        impact = event.get(
            "impact",
            "",
        )

        if (
            country
            not in MAJOR_CURRENCIES
        ):
            continue

        if impact != "High":
            continue

        event_time = (
            parse_event_time(
                event
            )
        )

        if event_time is None:
            continue

        prepared = dict(
            event
        )

        prepared[
            "_event_time"
        ] = event_time

        events.append(
            prepared
        )

    events.sort(
        key=lambda item:
            item["_event_time"]
    )

    return events


def send_daily_event_overview():
    now = datetime.now(
        timezone.utc
    )

    today = now.date()

    overview_key = (
        f"DAILY_OVERVIEW|"
        f"{today.isoformat()}"
    )

    if notification_sent(
        overview_key,
        "DAILY_OVERVIEW",
    ):
        return False

    events = (
        get_high_impact_events()
    )

    upcoming = []

    for event in events:
        event_time = event[
            "_event_time"
        ]

        if (
            event_time.date()
            != today
        ):
            continue

        if event_time <= now:
            continue

        upcoming.append(
            event
        )

    if not upcoming:
        mark_notification_sent(
            overview_key,
            "DAILY_OVERVIEW",
        )

        return False

    lines = [
        "📅 <b>AS · HIGH IMPACT TODAY</b>",
        "",
    ]

    for event in upcoming:
        event_time = (
            event["_event_time"]
        )

        country = event.get(
            "country",
            "",
        )

        title = event.get(
            "title",
            "Economic Event",
        )

        forecast = clean_value(
            event.get(
                "forecast"
            )
        )

        previous = clean_value(
            event.get(
                "previous"
            )
        )

        lines.extend(
            [
                (
                    f"🔴 <b>{country} · "
                    f"{title}</b>"
                ),
                (
                    "🕒 "
                    f"{event_time.strftime('%H:%M')} "
                    "UTC"
                ),
                (
                    "Forecast: "
                    f"<code>{forecast}</code>"
                ),
                (
                    "Previous: "
                    f"<code>{previous}</code>"
                ),
                "",
            ]
        )

    lines.extend(
        [
            "⚠️ High-impact events can "
            "increase market volatility.",
            "",
            "<b>AS | Forex & Crypto</b>",
            "@ASForexCrypto",
        ]
    )

    sent = send_free_message(
        "\n".join(
            lines
        )
    )

    if sent:
        mark_notification_sent(
            overview_key,
            "DAILY_OVERVIEW",
        )

        print(
            "CALENDAR DAILY OVERVIEW SENT | "
            f"Events={len(upcoming)}",
            flush=True,
        )

    return sent


def send_upcoming_event_warnings():
    now = datetime.now(
        timezone.utc
    )

    events = (
        get_high_impact_events()
    )

    sent_count = 0

    for event in events:
        event_time = event[
            "_event_time"
        ]

        seconds_until = (
            event_time - now
        ).total_seconds()

        minutes_until = (
            seconds_until / 60
        )

        if (
            minutes_until
            < WARNING_MIN_MINUTES
            or minutes_until
            > WARNING_MAX_MINUTES
        ):
            continue

        key = event_key(
            event,
            event_time,
        )

        if notification_sent(
            key,
            "30_MIN_WARNING",
        ):
            continue

        country = event.get(
            "country",
            "",
        )

        title = event.get(
            "title",
            "Economic Event",
        )

        forecast = clean_value(
            event.get(
                "forecast"
            )
        )

        previous = clean_value(
            event.get(
                "previous"
            )
        )

        text = (
            "⚠️ <b>HIGH IMPACT "
            "IN ~30 MIN</b>\n"
            "\n"
            f"🔴 <b>{country} · "
            f"{title}</b>\n"
            "\n"
            f"🕒 "
            f"<b>{event_time.strftime('%H:%M')} "
            f"UTC</b>\n"
            "\n"
            f"Forecast: "
            f"<code>{forecast}</code>\n"
            f"Previous: "
            f"<code>{previous}</code>\n"
            "\n"
            "Higher volatility may occur "
            "around the release.\n"
            "\n"
            "<b>AS | Forex & Crypto</b>\n"
            "@ASForexCrypto"
        )

        sent = send_free_message(
            text
        )

        if sent:
            mark_notification_sent(
                key,
                "30_MIN_WARNING",
            )

            sent_count += 1

            print(
                "CALENDAR WARNING SENT | "
                f"{country} | "
                f"{title} | "
                f"{event_time.strftime('%H:%M UTC')}",
                flush=True,
            )

    return sent_count


def process_economic_calendar():
    try:
        send_daily_event_overview()

        send_upcoming_event_warnings()

    except Exception as error:
        print(
            "CALENDAR ERROR: "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

        return False

    return True
