import os
import sqlite3

from datetime import (
    datetime,
    timedelta,
    timezone,
)

import requests

from config import (
    SYMBOLS,
)


BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

PRIVATE_CHANNEL_ID = os.getenv(
    "TELEGRAM_CHANNEL_ID"
)

DB_PATH = os.getenv(
    "DB_PATH",
    "/app/data/trading.db",
)


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

STALE_AFTER_MINUTES = 15

REMINDER_AFTER_MINUTES = 120


def get_connection():
    connection = sqlite3.connect(
        DB_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    return connection


def interval_minutes(
    interval
):
    if interval.endswith("min"):
        return int(
            interval.replace(
                "min",
                "",
            )
        )

    if interval.endswith("h"):
        return (
            int(
                interval.replace(
                    "h",
                    "",
                )
            )
            * 60
        )

    raise ValueError(
        f"Unsupported interval: "
        f"{interval}"
    )


def init_health_monitor_table():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            health_monitor_state (
                symbol TEXT
                PRIMARY KEY,

                status TEXT
                NOT NULL,

                last_candle_time TEXT,

                last_alert_at TEXT,

                updated_at TEXT
                NOT NULL
            )
            """
        )

        connection.commit()


def get_health_state(
    symbol
):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                symbol,
                status,
                last_candle_time,
                last_alert_at,
                updated_at

            FROM health_monitor_state

            WHERE symbol = ?

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


def save_health_state(
    symbol,
    status,
    last_candle_time,
    last_alert_at=None,
):
    updated_at = datetime.now(
        timezone.utc
    ).strftime(
        TIME_FORMAT
    )

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO health_monitor_state (
                symbol,
                status,
                last_candle_time,
                last_alert_at,
                updated_at
            )

            VALUES (?, ?, ?, ?, ?)

            ON CONFLICT(symbol)
            DO UPDATE SET
                status =
                    excluded.status,

                last_candle_time =
                    excluded.last_candle_time,

                last_alert_at =
                    excluded.last_alert_at,

                updated_at =
                    excluded.updated_at
            """,
            (
                symbol,
                status,
                last_candle_time,
                last_alert_at,
                updated_at,
            ),
        )

        connection.commit()


def get_latest_market_record(
    symbol
):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                candle_time,
                interval

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


def get_candle_confirmed_time(
    candle_time,
    interval,
):
    candle_open = datetime.strptime(
        candle_time,
        TIME_FORMAT,
    ).replace(
        tzinfo=timezone.utc
    )

    return (
        candle_open
        + timedelta(
            minutes=(
                interval_minutes(
                    interval
                )
            )
        )
    )


def forex_market_should_be_active(
    now
):
    weekday = now.weekday()

    # Monday - Thursday.
    if weekday in (
        0,
        1,
        2,
        3,
    ):
        return True

    # Friday:
    # stop health alerts after
    # approximately 22:00 UTC.
    if weekday == 4:
        return now.hour < 22

    # Saturday / Sunday.
    return False


def send_private_message(
    text
):
    if (
        not BOT_TOKEN
        or not PRIVATE_CHANNEL_ID
    ):
        print(
            "HEALTH MONITOR WARNING | "
            "Telegram token or private "
            "channel ID is missing",
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
                    PRIVATE_CHANNEL_ID,

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
                "HEALTH MONITOR "
                "TELEGRAM ERROR | "
                f"{data}",
                flush=True,
            )

            return False

        return True

    except Exception as error:
        print(
            "HEALTH MONITOR "
            "TELEGRAM ERROR | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

        return False


def build_stale_message(
    symbol,
    confirmed_time,
    age_minutes,
):
    return (
        "⚠️ <b>AS · SYSTEM ALERT</b>\n"
        "\n"
        f"💱 <b>{symbol}</b>\n"
        "\n"
        "Market data is not updating "
        "normally.\n"
        "\n"
        "🕒 Latest candle confirmed: "
        f"<b>{confirmed_time.strftime('%Y-%m-%d %H:%M UTC')}</b>\n"
        "\n"
        "⏳ Data age: "
        f"<b>{age_minutes:.0f} min</b>\n"
        "\n"
        "Alert threshold: "
        f"<b>{STALE_AFTER_MINUTES} min</b>\n"
        "\n"
        "<i>Private monitoring alert.</i>"
    )


def build_no_data_message(
    symbol
):
    return (
        "⚠️ <b>AS · SYSTEM ALERT</b>\n"
        "\n"
        f"💱 <b>{symbol}</b>\n"
        "\n"
        "No stored market data "
        "was found.\n"
        "\n"
        "<i>Private monitoring alert.</i>"
    )


def build_recovery_message(
    symbol,
    confirmed_time,
):
    return (
        "✅ <b>AS · SYSTEM RECOVERED</b>\n"
        "\n"
        f"💱 <b>{symbol}</b>\n"
        "\n"
        "Market data is updating "
        "again.\n"
        "\n"
        "🕒 Latest candle confirmed: "
        f"<b>{confirmed_time.strftime('%Y-%m-%d %H:%M UTC')}</b>\n"
        "\n"
        "<i>Private monitoring alert.</i>"
    )


def parse_saved_time(
    value
):
    if not value:
        return None

    try:
        return datetime.strptime(
            value,
            TIME_FORMAT,
        ).replace(
            tzinfo=timezone.utc
        )

    except ValueError:
        return None


def reminder_is_due(
    state,
    now
):
    if state is None:
        return True

    last_alert_at = (
        parse_saved_time(
            state.get(
                "last_alert_at"
            )
        )
    )

    if last_alert_at is None:
        return True

    elapsed = (
        now
        - last_alert_at
    )

    return (
        elapsed
        >= timedelta(
            minutes=(
                REMINDER_AFTER_MINUTES
            )
        )
    )


def process_symbol_health(
    symbol,
    now,
):
    state = get_health_state(
        symbol
    )

    latest = (
        get_latest_market_record(
            symbol
        )
    )

    # =========================
    # NO DATA
    # =========================

    if latest is None:
        previous_status = (
            state[
                "status"
            ]
            if state
            else None
        )

        should_alert = (
            previous_status
            != "NO_DATA"

            or reminder_is_due(
                state,
                now,
            )
        )

        if should_alert:
            sent = (
                send_private_message(
                    build_no_data_message(
                        symbol
                    )
                )
            )

            if sent:
                alert_time = (
                    now.strftime(
                        TIME_FORMAT
                    )
                )

                save_health_state(
                    symbol=symbol,
                    status="NO_DATA",
                    last_candle_time=None,
                    last_alert_at=(
                        alert_time
                    ),
                )

                print(
                    "HEALTH ALERT SENT | "
                    f"{symbol} | "
                    "No market data",
                    flush=True,
                )

        return

    confirmed_time = (
        get_candle_confirmed_time(
            latest[
                "candle_time"
            ],
            latest[
                "interval"
            ],
        )
    )

    age = (
        now
        - confirmed_time
    )

    age_minutes = max(
        age.total_seconds()
        / 60,
        0.0,
    )

    latest_candle_time = (
        latest[
            "candle_time"
        ]
    )

    previous_status = (
        state[
            "status"
        ]
        if state
        else None
    )

    # =========================
    # HEALTHY
    # =========================

    if (
        age_minutes
        < STALE_AFTER_MINUTES
    ):
        if previous_status in (
            "STALE",
            "NO_DATA",
        ):
            sent = (
                send_private_message(
                    build_recovery_message(
                        symbol,
                        confirmed_time,
                    )
                )
            )

            if sent:
                print(
                    "HEALTH RECOVERED | "
                    f"{symbol} | "
                    "Latest="
                    f"{confirmed_time.strftime(TIME_FORMAT)} UTC",
                    flush=True,
                )

        save_health_state(
            symbol=symbol,
            status="HEALTHY",
            last_candle_time=(
                latest_candle_time
            ),
            last_alert_at=None,
        )

        return

    # =========================
    # STALE
    # =========================

    should_alert = (
        previous_status
        != "STALE"

        or reminder_is_due(
            state,
            now,
        )
    )

    if not should_alert:
        save_health_state(
            symbol=symbol,
            status="STALE",
            last_candle_time=(
                latest_candle_time
            ),
            last_alert_at=(
                state[
                    "last_alert_at"
                ]
                if state
                else None
            ),
        )

        return

    message = build_stale_message(
        symbol=symbol,
        confirmed_time=(
            confirmed_time
        ),
        age_minutes=(
            age_minutes
        ),
    )

    sent = send_private_message(
        message
    )

    if sent:
        alert_time = (
            now.strftime(
                TIME_FORMAT
            )
        )

        save_health_state(
            symbol=symbol,
            status="STALE",
            last_candle_time=(
                latest_candle_time
            ),
            last_alert_at=(
                alert_time
            ),
        )

        print(
            "HEALTH ALERT SENT | "
            f"{symbol} | "
            "Status=STALE | "
            f"Age={age_minutes:.1f}m",
            flush=True,
        )


def process_health_monitor():
    init_health_monitor_table()

    now = datetime.now(
        timezone.utc
    )

    if not forex_market_should_be_active(
        now
    ):
        return False

    for symbol in SYMBOLS:
        try:
            process_symbol_health(
                symbol,
                now,
            )

        except Exception as error:
            print(
                "HEALTH MONITOR ERROR | "
                f"{symbol} | "
                f"{type(error).__name__}: "
                f"{error}",
                flush=True,
            )

    return True
