import os
import sqlite3
import threading
import time

import requests

from user_subscriptions import (
    register_or_update_user,
    get_effective_access,
)


BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "",
)

DB_PATH = os.getenv(
    "DB_PATH",
    "/app/data/trading.db",
)

API_BASE_URL = (
    f"https://api.telegram.org/"
    f"bot{BOT_TOKEN}"
)

POLL_TIMEOUT_SECONDS = 25

RETRY_SECONDS = 5

STATE_KEY = "last_update_id"


_polling_started = False
_polling_lock = threading.Lock()


def get_connection():
    connection = sqlite3.connect(
        DB_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    return connection


def init_user_bot_state_table():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            telegram_user_bot_state (
                state_key TEXT
                PRIMARY KEY,

                state_value TEXT
                NOT NULL
            )
            """
        )

        connection.commit()


def get_last_update_id():
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                state_value

            FROM telegram_user_bot_state

            WHERE state_key = ?
            """,
            (
                STATE_KEY,
            ),
        ).fetchone()

    if row is None:
        return None

    try:
        return int(
            row[
                "state_value"
            ]
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


def save_last_update_id(
    update_id
):
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO
            telegram_user_bot_state (
                state_key,
                state_value
            )

            VALUES (?, ?)

            ON CONFLICT (
                state_key
            )

            DO UPDATE SET
                state_value =
                    excluded.state_value
            """,
            (
                STATE_KEY,
                str(
                    int(
                        update_id
                    )
                ),
            ),
        )

        connection.commit()


def telegram_request(
    method,
    payload=None,
    timeout=15,
):
    if not BOT_TOKEN:
        return None

    try:
        response = requests.post(
            f"{API_BASE_URL}/{method}",
            json=payload or {},
            timeout=timeout,
        )

        response.raise_for_status()

        data = response.json()

        if not data.get(
            "ok"
        ):
            print(
                "USER BOT TELEGRAM ERROR | "
                f"{method} | "
                f"{data}",
                flush=True,
            )

            return None

        return data

    except Exception as error:
        print(
            "USER BOT REQUEST ERROR | "
            f"{method} | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

        return None


def send_private_message(
    chat_id,
    text,
):
    result = telegram_request(
        "sendMessage",
        {
            "chat_id":
                chat_id,

            "text":
                text,

            "parse_mode":
                "HTML",

            "disable_web_page_preview":
                True,
        },
    )

    return (
        result is not None
    )


def format_username(
    username
):
    if not username:
        return "-"

    return (
        "@"
        + username
    )


def start_text(
    first_name=None,
):
    if first_name:
        greeting = (
            f"Hello, "
            f"{first_name}!"
        )
    else:
        greeting = (
            "Hello!"
        )

    return (
        f"👋 <b>{greeting}</b>\n"
        "\n"
        "Welcome to "
        "<b>AS | Forex & Crypto</b>.\n"
        "\n"
        "📊 Market analysis\n"
        "📈 Forex & Crypto research\n"
        "🔔 Trading signals\n"
        "📉 Transparent results\n"
        "\n"
        "Your current plan: "
        "<b>FREE</b>\n"
        "\n"
        "Available commands:\n"
        "/status — subscription status\n"
        "/vip — VIP information\n"
        "/help — help\n"
        "\n"
        "⚠️ The project is currently "
        "in the testing and research stage.\n"
        "\n"
        "<i>No guaranteed profits. "
        "Trading involves risk.</i>"
    )


def status_text(
    telegram_user_id
):
    access = (
        get_effective_access(
            telegram_user_id
        )
    )

    if access is None:
        return (
            "Your account is not "
            "registered yet.\n\n"
            "Use /start first."
        )

    if (
        access[
            "plan"
        ]
        == "VIP"
    ):
        expires_at = (
            access.get(
                "expires_at"
            )
            or "No expiration"
        )

        return (
            "👤 <b>Subscription status</b>\n"
            "\n"
            "Plan: <b>VIP</b>\n"
            "Status: <b>ACTIVE</b>\n"
            f"Expires: "
            f"<b>{expires_at} UTC</b>"
        )

    return (
        "👤 <b>Subscription status</b>\n"
        "\n"
        "Plan: <b>FREE</b>\n"
        "Status: <b>ACTIVE</b>\n"
        "Expiration: <b>None</b>"
    )


def vip_text():
    return (
        "⭐ <b>AS VIP</b>\n"
        "\n"
        "VIP is being prepared while "
        "the trading strategies are "
        "still under research and testing.\n"
        "\n"
        "VIP access is currently "
        "<b>not available for purchase</b>.\n"
        "\n"
        "When the service is ready, "
        "subscription and access options "
        "will appear here."
    )


def help_text():
    return (
        "ℹ️ <b>AS Bot Help</b>\n"
        "\n"
        "/start — register / open menu\n"
        "/status — subscription status\n"
        "/vip — VIP information\n"
        "/help — this message"
    )


def unknown_text():
    return (
        "I don't recognize that command.\n"
        "\n"
        "Use /help to see "
        "available commands."
    )


def register_message_user(
    message
):
    user_data = (
        message.get(
            "from"
        )
        or {}
    )

    telegram_user_id = (
        user_data.get(
            "id"
        )
    )

    if telegram_user_id is None:
        return None

    return register_or_update_user(
        telegram_user_id=(
            telegram_user_id
        ),

        username=(
            user_data.get(
                "username"
            )
        ),

        first_name=(
            user_data.get(
                "first_name"
            )
        ),

        last_name=(
            user_data.get(
                "last_name"
            )
        ),
    )


def normalize_command(
    text
):
    if not text:
        return ""

    first_part = (
        text.strip()
        .split()[0]
        .lower()
    )

    if "@" in first_part:
        first_part = (
            first_part
            .split(
                "@",
                1,
            )[0]
        )

    return first_part


def process_private_message(
    message
):
    chat = (
        message.get(
            "chat"
        )
        or {}
    )

    if (
        chat.get(
            "type"
        )
        != "private"
    ):
        return

    telegram_user = (
        message.get(
            "from"
        )
        or {}
    )

    telegram_user_id = (
        telegram_user.get(
            "id"
        )
    )

    chat_id = (
        chat.get(
            "id"
        )
    )

    if (
        telegram_user_id is None
        or chat_id is None
    ):
        return

    user = (
        register_message_user(
            message
        )
    )

    if user is None:
        return

    text = (
        message.get(
            "text"
        )
        or ""
    )

    command = (
        normalize_command(
            text
        )
    )

    if command == "/start":
        send_private_message(
            chat_id,
            start_text(
                telegram_user.get(
                    "first_name"
                )
            ),
        )

        print(
            "USER BOT | "
            "/start | "
            f"UserDB="
            f"{user['id']} | "
            "Plan=FREE",
            flush=True,
        )

        return

    if command == "/status":
        send_private_message(
            chat_id,
            status_text(
                telegram_user_id
            ),
        )

        print(
            "USER BOT | "
            "/status | "
            f"UserDB="
            f"{user['id']}",
            flush=True,
        )

        return

    if command == "/vip":
        send_private_message(
            chat_id,
            vip_text(),
        )

        print(
            "USER BOT | "
            "/vip | "
            f"UserDB="
            f"{user['id']}",
            flush=True,
        )

        return

    if command == "/help":
        send_private_message(
            chat_id,
            help_text(),
        )

        return

    send_private_message(
        chat_id,
        unknown_text(),
    )


def process_update(
    update
):
    message = (
        update.get(
            "message"
        )
    )

    if message is None:
        return

    process_private_message(
        message
    )


def fetch_updates(
    offset=None,
):
    payload = {
        "timeout":
            POLL_TIMEOUT_SECONDS,

        "allowed_updates":
            [
                "message",
            ],
    }

    if offset is not None:
        payload[
            "offset"
        ] = offset

    return telegram_request(
        "getUpdates",
        payload,
        timeout=(
            POLL_TIMEOUT_SECONDS
            + 10
        ),
    )


def polling_loop():
    init_user_bot_state_table()

    last_update_id = (
        get_last_update_id()
    )

    if last_update_id is None:
        offset = None
    else:
        offset = (
            last_update_id
            + 1
        )

    print(
        "User bot: polling enabled",
        flush=True,
    )

    while True:
        try:
            result = (
                fetch_updates(
                    offset=offset
                )
            )

            if result is None:
                time.sleep(
                    RETRY_SECONDS
                )

                continue

            updates = (
                result.get(
                    "result"
                )
                or []
            )

            for update in updates:
                update_id = (
                    update.get(
                        "update_id"
                    )
                )

                if update_id is None:
                    continue

                try:
                    process_update(
                        update
                    )

                except Exception as error:
                    print(
                        "USER BOT UPDATE ERROR | "
                        f"{type(error).__name__}: "
                        f"{error}",
                        flush=True,
                    )

                save_last_update_id(
                    update_id
                )

                offset = (
                    update_id
                    + 1
                )

        except Exception as error:
            print(
                "USER BOT LOOP ERROR | "
                f"{type(error).__name__}: "
                f"{error}",
                flush=True,
            )

            time.sleep(
                RETRY_SECONDS
            )


def start_user_bot_polling():
    global _polling_started

    with _polling_lock:
        if _polling_started:
            return False

        if not BOT_TOKEN:
            print(
                "User bot: disabled, "
                "TELEGRAM_BOT_TOKEN missing",
                flush=True,
            )

            return False

        thread = threading.Thread(
            target=polling_loop,
            name="telegram-user-bot",
            daemon=True,
        )

        thread.start()

        _polling_started = True

    return True
