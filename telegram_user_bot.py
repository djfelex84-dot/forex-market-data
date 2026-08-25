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

FREE_CHANNEL_USERNAME = os.getenv(
    "TELEGRAM_FREE_CHANNEL_ID",
    "@ASForexCrypto",
)

API_BASE_URL = (
    f"https://api.telegram.org/"
    f"bot{BOT_TOKEN}"
)

POLL_TIMEOUT_SECONDS = 25

RETRY_SECONDS = 5

STATE_KEY = "last_update_id"


BUTTON_STATUS = "📊 My Status"
BUTTON_VIP = "⭐ VIP"
BUTTON_FREE = "📢 Free Channel"
BUTTON_ABOUT = "ℹ️ About"
BUTTON_HELP = "❓ Help"


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


def main_keyboard():
    return {
        "keyboard": [
            [
                {
                    "text":
                        BUTTON_STATUS
                },
                {
                    "text":
                        BUTTON_VIP
                },
            ],
            [
                {
                    "text":
                        BUTTON_FREE
                },
                {
                    "text":
                        BUTTON_ABOUT
                },
            ],
            [
                {
                    "text":
                        BUTTON_HELP
                },
            ],
        ],

        "resize_keyboard":
            True,

        "one_time_keyboard":
            False,

        "is_persistent":
            True,

        "input_field_placeholder":
            "Choose an option",
    }


def send_private_message(
    chat_id,
    text,
    show_keyboard=True,
):
    payload = {
        "chat_id":
            chat_id,

        "text":
            text,

        "parse_mode":
            "HTML",

        "disable_web_page_preview":
            True,
    }

    if show_keyboard:
        payload[
            "reply_markup"
        ] = main_keyboard()

    result = telegram_request(
        "sendMessage",
        payload,
    )

    return (
        result is not None
    )


def set_bot_commands():
    commands = [
        {
            "command":
                "start",

            "description":
                "Open AS bot",
        },
        {
            "command":
                "status",

            "description":
                "My subscription",
        },
        {
            "command":
                "vip",

            "description":
                "VIP information",
        },
        {
            "command":
                "channel",

            "description":
                "Open Free channel",
        },
        {
            "command":
                "about",

            "description":
                "About AS",
        },
        {
            "command":
                "help",

            "description":
                "Help",
        },
    ]

    result = telegram_request(
        "setMyCommands",
        {
            "commands":
                commands,
        },
    )

    if result is None:
        print(
            "User bot: "
            "command menu setup failed",
            flush=True,
        )

        return False

    print(
        "User bot: "
        "command menu configured",
        flush=True,
    )

    return True


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
        "Use the buttons below "
        "to explore the service.\n"
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
            "Press START first."
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
            "👤 <b>My Status</b>\n"
            "\n"
            "Plan: <b>VIP</b>\n"
            "Status: <b>ACTIVE</b>\n"
            f"Expires: "
            f"<b>{expires_at} UTC</b>"
        )

    return (
        "👤 <b>My Status</b>\n"
        "\n"
        "Plan: <b>FREE</b>\n"
        "Status: <b>ACTIVE</b>\n"
        "Expiration: <b>None</b>"
    )


def vip_text():
    return (
        "⭐ <b>AS VIP</b>\n"
        "\n"
        "VIP will provide access to "
        "selected trading signals "
        "with Entry, Stop Loss, "
        "Take Profit and transparent "
        "results.\n"
        "\n"
        "The strategy is currently "
        "being researched and tested.\n"
        "\n"
        "VIP access is therefore "
        "<b>not available for purchase yet</b>.\n"
        "\n"
        "When the service is ready, "
        "subscription options will "
        "appear directly in this bot."
    )


def free_channel_text():
    username = (
        FREE_CHANNEL_USERNAME
        or "@ASForexCrypto"
    )

    if username.startswith(
        "@"
    ):
        clean_username = (
            username[
                1:
            ]
        )

        channel_url = (
            "https://t.me/"
            f"{clean_username}"
        )

        display_name = (
            username
        )

    elif username.startswith(
        "https://"
    ):
        channel_url = (
            username
        )

        display_name = (
            "AS | Forex & Crypto"
        )

    else:
        channel_url = (
            "https://t.me/"
            "ASForexCrypto"
        )

        display_name = (
            "@ASForexCrypto"
        )

    return (
        "📢 <b>AS Free Channel</b>\n"
        "\n"
        "Market analysis, news, "
        "research updates and "
        "transparent statistics.\n"
        "\n"
        f"👉 <a href=\"{channel_url}\">"
        f"Open {display_name}</a>"
    )


def about_text():
    return (
        "ℹ️ <b>About AS</b>\n"
        "\n"
        "<b>AS | Forex & Crypto</b> "
        "is being developed as a "
        "data-driven market analysis "
        "and trading signal service.\n"
        "\n"
        "Our focus:\n"
        "• clear Entry / SL / TP\n"
        "• transparent results\n"
        "• losses are not hidden\n"
        "• no guaranteed profits\n"
        "• strategy testing before launch\n"
        "\n"
        "All market times are shown "
        "in <b>UTC</b>."
    )


def help_text():
    return (
        "❓ <b>Help</b>\n"
        "\n"
        "You can use the buttons "
        "at the bottom of the chat.\n"
        "\n"
        "📊 <b>My Status</b> — "
        "your current subscription\n"
        "\n"
        "⭐ <b>VIP</b> — "
        "VIP information\n"
        "\n"
        "📢 <b>Free Channel</b> — "
        "open the public channel\n"
        "\n"
        "ℹ️ <b>About</b> — "
        "information about AS\n"
        "\n"
        "You can also use:\n"
        "/start\n"
        "/status\n"
        "/vip\n"
        "/channel\n"
        "/about\n"
        "/help"
    )


def unknown_text():
    return (
        "I don't recognize that option.\n"
        "\n"
        "Please use the buttons below."
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


def normalize_button_text(
    text
):
    if not text:
        return ""

    return text.strip()


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

    button_text = (
        normalize_button_text(
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

        access = (
            get_effective_access(
                telegram_user_id
            )
        )

        plan = (
            access.get(
                "plan"
            )
            if access
            else "FREE"
        )

        print(
            "USER BOT | "
            "/start | "
            f"UserDB="
            f"{user['id']} | "
            f"Plan="
            f"{plan}",
            flush=True,
        )

        return

    if (
        command == "/status"
        or button_text == BUTTON_STATUS
    ):
        send_private_message(
            chat_id,
            status_text(
                telegram_user_id
            ),
        )

        print(
            "USER BOT | "
            "STATUS | "
            f"UserDB="
            f"{user['id']}",
            flush=True,
        )

        return

    if (
        command == "/vip"
        or button_text == BUTTON_VIP
    ):
        send_private_message(
            chat_id,
            vip_text(),
        )

        print(
            "USER BOT | "
            "VIP | "
            f"UserDB="
            f"{user['id']}",
            flush=True,
        )

        return

    if (
        command == "/channel"
        or button_text == BUTTON_FREE
    ):
        send_private_message(
            chat_id,
            free_channel_text(),
        )

        print(
            "USER BOT | "
            "FREE CHANNEL | "
            f"UserDB="
            f"{user['id']}",
            flush=True,
        )

        return

    if (
        command == "/about"
        or button_text == BUTTON_ABOUT
    ):
        send_private_message(
            chat_id,
            about_text(),
        )

        return

    if (
        command == "/help"
        or button_text == BUTTON_HELP
    ):
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

    set_bot_commands()

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
