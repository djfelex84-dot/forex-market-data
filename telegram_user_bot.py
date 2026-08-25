import os
import sqlite3
import threading
import time
import html

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

CALLBACK_MENU = "MENU"
CALLBACK_STATUS = "STATUS"
CALLBACK_VIP = "VIP"
CALLBACK_ABOUT = "ABOUT"
CALLBACK_HELP = "HELP"


_polling_started = False
_polling_lock = threading.Lock()


# ============================================================
# DATABASE
# ============================================================

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


# ============================================================
# TELEGRAM API
# ============================================================

def telegram_request(
    method,
    payload=None,
    timeout=15,
):
    if not BOT_TOKEN:
        print(
            "USER BOT ERROR | "
            "TELEGRAM_BOT_TOKEN missing",
            flush=True,
        )

        return None

    try:
        response = requests.post(
            f"{API_BASE_URL}/{method}",
            json=payload or {},
            timeout=timeout,
        )

    except Exception as error:
        print(
            "USER BOT REQUEST ERROR | "
            f"{method} | "
            f"{type(error).__name__}",
            flush=True,
        )

        return None

    if response.status_code >= 400:
        print(
            "USER BOT REQUEST ERROR | "
            f"{method} | "
            f"HTTP "
            f"{response.status_code} "
            f"{response.reason}",
            flush=True,
        )

        return None

    try:
        data = response.json()

    except Exception:
        print(
            "USER BOT REQUEST ERROR | "
            f"{method} | "
            "Invalid JSON response",
            flush=True,
        )

        return None

    if not data.get(
        "ok"
    ):
        print(
            "USER BOT TELEGRAM ERROR | "
            f"{method}",
            flush=True,
        )

        return None

    return data


def answer_callback_query(
    callback_query_id
):
    if not callback_query_id:
        return False

    result = telegram_request(
        "answerCallbackQuery",
        {
            "callback_query_id":
                callback_query_id,
        },
    )

    return (
        result is not None
    )


def send_inline_message(
    chat_id,
    text,
    reply_markup=None,
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

    if reply_markup is not None:
        payload[
            "reply_markup"
        ] = reply_markup

    result = telegram_request(
        "sendMessage",
        payload,
    )

    return result


def edit_inline_message(
    chat_id,
    message_id,
    text,
    reply_markup=None,
):
    payload = {
        "chat_id":
            chat_id,

        "message_id":
            message_id,

        "text":
            text,

        "parse_mode":
            "HTML",

        "disable_web_page_preview":
            True,
    }

    if reply_markup is not None:
        payload[
            "reply_markup"
        ] = reply_markup

    result = telegram_request(
        "editMessageText",
        payload,
    )

    return (
        result is not None
    )


# ============================================================
# LANGUAGE
# ============================================================

def get_language(
    telegram_user
):
    language_code = (
        telegram_user.get(
            "language_code"
        )
        or ""
    ).lower()

    if language_code.startswith(
        "ru"
    ):
        return "ru"

    return "en"


# ============================================================
# FREE CHANNEL
# ============================================================

def get_free_channel_url():
    value = (
        FREE_CHANNEL_USERNAME
        or "@ASForexCrypto"
    )

    if value.startswith(
        "@"
    ):
        return (
            "https://t.me/"
            + value[1:]
        )

    if value.startswith(
        "https://"
    ):
        return value

    return (
        "https://t.me/"
        "ASForexCrypto"
    )


# ============================================================
# INLINE KEYBOARDS
# ============================================================

def start_keyboard(
    language
):
    if language == "ru":
        button_text = (
            "🚀 ОТКРЫТЬ МЕНЮ"
        )

    else:
        button_text = (
            "🚀 OPEN MENU"
        )

    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        button_text,

                    "callback_data":
                        CALLBACK_MENU,
                }
            ]
        ]
    }


def main_menu_keyboard(
    language
):
    if language == "ru":
        status_text = (
            "📊 Мой статус"
        )

        vip_text = (
            "⭐ VIP"
        )

        channel_text = (
            "📢 Бесплатный канал"
        )

        about_text = (
            "ℹ️ О проекте"
        )

        help_text = (
            "❓ Помощь"
        )

    else:
        status_text = (
            "📊 My Status"
        )

        vip_text = (
            "⭐ VIP"
        )

        channel_text = (
            "📢 Free Channel"
        )

        about_text = (
            "ℹ️ About"
        )

        help_text = (
            "❓ Help"
        )

    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        status_text,

                    "callback_data":
                        CALLBACK_STATUS,
                }
            ],

            [
                {
                    "text":
                        vip_text,

                    "callback_data":
                        CALLBACK_VIP,
                }
            ],

            [
                {
                    "text":
                        channel_text,

                    "url":
                        get_free_channel_url(),
                }
            ],

            [
                {
                    "text":
                        about_text,

                    "callback_data":
                        CALLBACK_ABOUT,
                }
            ],

            [
                {
                    "text":
                        help_text,

                    "callback_data":
                        CALLBACK_HELP,
                }
            ],
        ]
    }


def back_to_menu_keyboard(
    language
):
    if language == "ru":
        button_text = (
            "🏠 Главное меню"
        )

    else:
        button_text = (
            "🏠 Main Menu"
        )

    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        button_text,

                    "callback_data":
                        CALLBACK_MENU,
                }
            ]
        ]
    }


def channel_keyboard(
    language
):
    if language == "ru":
        open_text = (
            "📢 Открыть канал"
        )

        back_text = (
            "🏠 Главное меню"
        )

    else:
        open_text = (
            "📢 Open Channel"
        )

        back_text = (
            "🏠 Main Menu"
        )

    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        open_text,

                    "url":
                        get_free_channel_url(),
                }
            ],

            [
                {
                    "text":
                        back_text,

                    "callback_data":
                        CALLBACK_MENU,
                }
            ],
        ]
    }


# ============================================================
# TEXTS
# ============================================================

def start_text(
    language,
    first_name,
    plan,
):
    safe_name = html.escape(
        first_name
        or ""
    )

    safe_plan = html.escape(
        plan
        or "FREE"
    )

    if language == "ru":
        if safe_name:
            greeting = (
                f"Привет, {safe_name}!"
            )
        else:
            greeting = (
                "Привет!"
            )

        return (
            f"👋 <b>{greeting}</b>\n"
            "\n"
            "Добро пожаловать в "
            "<b>AS | Forex & Crypto</b>.\n"
            "\n"
            "Ваш текущий тариф: "
            f"<b>{safe_plan}</b>\n"
            "\n"
            "Нажмите кнопку ниже, "
            "чтобы открыть меню.\n"
            "\n"
            "⚠️ Проект сейчас находится "
            "на этапе тестирования "
            "и исследования.\n"
            "\n"
            "<i>Гарантированной прибыли нет. "
            "Торговля связана с риском.</i>"
        )

    if safe_name:
        greeting = (
            f"Hello, {safe_name}!"
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
        "Your current plan: "
        f"<b>{safe_plan}</b>\n"
        "\n"
        "Press the button below "
        "to open the menu.\n"
        "\n"
        "⚠️ The project is currently "
        "in the testing and "
        "research stage.\n"
        "\n"
        "<i>No guaranteed profits. "
        "Trading involves risk.</i>"
    )


def main_menu_text(
    language
):
    if language == "ru":
        return (
            "🏠 <b>AS | Forex & Crypto</b>\n"
            "\n"
            "Выберите нужный раздел:"
        )

    return (
        "🏠 <b>AS | Forex & Crypto</b>\n"
        "\n"
        "Choose a section:"
    )


def status_text(
    telegram_user_id,
    language,
):
    access = (
        get_effective_access(
            telegram_user_id
        )
    )

    if access is None:
        if language == "ru":
            return (
                "Аккаунт ещё не зарегистрирован.\n"
                "\n"
                "Нажмите START."
            )

        return (
            "Your account is not "
            "registered yet.\n"
            "\n"
            "Press START."
        )

    plan = (
        access.get(
            "plan"
        )
        or "FREE"
    )

    expires_at = (
        access.get(
            "expires_at"
        )
    )

    if language == "ru":
        if plan == "VIP":
            expires_text = (
                expires_at
                or "Без ограничения"
            )

            return (
                "👤 <b>Мой статус</b>\n"
                "\n"
                "Тариф: <b>VIP</b>\n"
                "Статус: <b>ACTIVE</b>\n"
                "Действует до: "
                f"<b>{html.escape(str(expires_text))} UTC</b>"
            )

        return (
            "👤 <b>Мой статус</b>\n"
            "\n"
            "Тариф: <b>FREE</b>\n"
            "Статус: <b>ACTIVE</b>\n"
            "Срок действия: "
            "<b>без ограничения</b>"
        )

    if plan == "VIP":
        expires_text = (
            expires_at
            or "No expiration"
        )

        return (
            "👤 <b>My Status</b>\n"
            "\n"
            "Plan: <b>VIP</b>\n"
            "Status: <b>ACTIVE</b>\n"
            "Expires: "
            f"<b>{html.escape(str(expires_text))} UTC</b>"
        )

    return (
        "👤 <b>My Status</b>\n"
        "\n"
        "Plan: <b>FREE</b>\n"
        "Status: <b>ACTIVE</b>\n"
        "Expiration: <b>None</b>"
    )


def vip_text(
    language
):
    if language == "ru":
        return (
            "⭐ <b>AS VIP</b>\n"
            "\n"
            "VIP будет предоставлять доступ "
            "к торговым сигналам с:\n"
            "\n"
            "🎯 Entry\n"
            "🛑 Stop Loss\n"
            "🏁 Take Profit\n"
            "📊 прозрачными результатами\n"
            "\n"
            "Сейчас стратегия ещё проходит "
            "исследование и тестирование.\n"
            "\n"
            "Поэтому покупка VIP пока "
            "<b>недоступна</b>.\n"
            "\n"
            "Когда сервис будет готов, "
            "оформить подписку можно будет "
            "прямо через этого бота."
        )

    return (
        "⭐ <b>AS VIP</b>\n"
        "\n"
        "VIP will provide access "
        "to trading signals with:\n"
        "\n"
        "🎯 Entry\n"
        "🛑 Stop Loss\n"
        "🏁 Take Profit\n"
        "📊 transparent results\n"
        "\n"
        "The strategy is currently "
        "being researched and tested.\n"
        "\n"
        "VIP access is therefore "
        "<b>not available for purchase yet</b>.\n"
        "\n"
        "When the service is ready, "
        "subscription options will appear "
        "directly in this bot."
    )


def about_text(
    language
):
    if language == "ru":
        return (
            "ℹ️ <b>О проекте AS</b>\n"
            "\n"
            "<b>AS | Forex & Crypto</b> — "
            "сервис анализа рынка "
            "и торговых сигналов, "
            "который сейчас находится "
            "в разработке.\n"
            "\n"
            "Наши принципы:\n"
            "\n"
            "• понятные Entry / SL / TP\n"
            "• прозрачная статистика\n"
            "• убыточные сделки не скрываются\n"
            "• никаких обещаний гарантированной прибыли\n"
            "• сначала тестирование, потом запуск\n"
            "\n"
            "🕒 Рыночное время отображается "
            "в <b>UTC</b>."
        )

    return (
        "ℹ️ <b>About AS</b>\n"
        "\n"
        "<b>AS | Forex & Crypto</b> "
        "is being developed as a "
        "market analysis and trading "
        "signal service.\n"
        "\n"
        "Our principles:\n"
        "\n"
        "• clear Entry / SL / TP\n"
        "• transparent statistics\n"
        "• losing trades are not hidden\n"
        "• no guaranteed profit promises\n"
        "• testing before launch\n"
        "\n"
        "🕒 Market times are shown "
        "in <b>UTC</b>."
    )


def help_text(
    language
):
    if language == "ru":
        return (
            "❓ <b>Помощь</b>\n"
            "\n"
            "Для управления ботом "
            "используйте кнопки меню.\n"
            "\n"
            "📊 <b>Мой статус</b> — "
            "ваш текущий тариф\n"
            "\n"
            "⭐ <b>VIP</b> — "
            "информация о VIP\n"
            "\n"
            "📢 <b>Бесплатный канал</b> — "
            "публичный Telegram-канал\n"
            "\n"
            "ℹ️ <b>О проекте</b> — "
            "информация об AS\n"
            "\n"
            "Также доступны команды:\n"
            "/start\n"
            "/status\n"
            "/vip\n"
            "/channel\n"
            "/about\n"
            "/help"
        )

    return (
        "❓ <b>Help</b>\n"
        "\n"
        "Use the menu buttons "
        "to control the bot.\n"
        "\n"
        "📊 <b>My Status</b> — "
        "your current plan\n"
        "\n"
        "⭐ <b>VIP</b> — "
        "VIP information\n"
        "\n"
        "📢 <b>Free Channel</b> — "
        "public Telegram channel\n"
        "\n"
        "ℹ️ <b>About</b> — "
        "information about AS\n"
        "\n"
        "Commands are also available:\n"
        "/start\n"
        "/status\n"
        "/vip\n"
        "/channel\n"
        "/about\n"
        "/help"
    )


def channel_text(
    language
):
    if language == "ru":
        return (
            "📢 <b>Бесплатный канал</b>\n"
            "\n"
            "Анализ рынка, новости, "
            "исследования и прозрачная "
            "статистика.\n"
            "\n"
            "Нажмите кнопку ниже, "
            "чтобы открыть канал."
        )

    return (
        "📢 <b>Free Channel</b>\n"
        "\n"
        "Market analysis, news, "
        "research and transparent "
        "statistics.\n"
        "\n"
        "Press the button below "
        "to open the channel."
    )


def unknown_text(
    language
):
    if language == "ru":
        return (
            "Я не распознал эту команду.\n"
            "\n"
            "Откройте главное меню."
        )

    return (
        "I don't recognize that command.\n"
        "\n"
        "Open the main menu."
    )


# ============================================================
# USER REGISTRATION
# ============================================================

def register_telegram_user(
    telegram_user
):
    telegram_user_id = (
        telegram_user.get(
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
            telegram_user.get(
                "username"
            )
        ),

        first_name=(
            telegram_user.get(
                "first_name"
            )
        ),

        last_name=(
            telegram_user.get(
                "last_name"
            )
        ),
    )


# ============================================================
# START
# ============================================================

def send_start_screen(
    chat_id,
    telegram_user,
):
    language = get_language(
        telegram_user
    )

    telegram_user_id = (
        telegram_user.get(
            "id"
        )
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

    payload = {
        "chat_id":
            chat_id,

        "text":
            start_text(
                language=language,
                first_name=(
                    telegram_user.get(
                        "first_name"
                    )
                ),
                plan=plan,
            ),

        "parse_mode":
            "HTML",

        "disable_web_page_preview":
            True,

        # Removes the old persistent
        # reply keyboard from the
        # previous bot version.
        "reply_markup": {
            "remove_keyboard":
                True,
        },
    }

    result = telegram_request(
        "sendMessage",
        payload,
    )

    if result is None:
        return False

    message = (
        result.get(
            "result"
        )
        or {}
    )

    message_id = (
        message.get(
            "message_id"
        )
    )

    if message_id is None:
        return True

    # Add the large inline OPEN MENU
    # button to the same message.
    edit_result = telegram_request(
        "editMessageReplyMarkup",
        {
            "chat_id":
                chat_id,

            "message_id":
                message_id,

            "reply_markup":
                start_keyboard(
                    language
                ),
        },
    )

    if edit_result is None:
        send_inline_message(
            chat_id=chat_id,
            text=(
                "🚀"
                if language == "en"
                else "🚀"
            ),
            reply_markup=(
                start_keyboard(
                    language
                )
            ),
        )

    return True


# ============================================================
# COMMANDS
# ============================================================

def set_bot_commands():
    english_commands = [
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
                "Free channel",
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

    russian_commands = [
        {
            "command":
                "start",

            "description":
                "Открыть бота AS",
        },
        {
            "command":
                "status",

            "description":
                "Моя подписка",
        },
        {
            "command":
                "vip",

            "description":
                "Информация о VIP",
        },
        {
            "command":
                "channel",

            "description":
                "Бесплатный канал",
        },
        {
            "command":
                "about",

            "description":
                "О проекте AS",
        },
        {
            "command":
                "help",

            "description":
                "Помощь",
        },
    ]

    english_result = telegram_request(
        "setMyCommands",
        {
            "commands":
                english_commands,
        },
    )

    russian_result = telegram_request(
        "setMyCommands",
        {
            "commands":
                russian_commands,

            "language_code":
                "ru",
        },
    )

    if (
        english_result is None
        or russian_result is None
    ):
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


# ============================================================
# MESSAGE HANDLER
# ============================================================

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

    user = register_telegram_user(
        telegram_user
    )

    if user is None:
        return

    language = get_language(
        telegram_user
    )

    text = (
        message.get(
            "text"
        )
        or ""
    )

    command = normalize_command(
        text
    )

    if command == "/start":
        send_start_screen(
            chat_id=chat_id,
            telegram_user=(
                telegram_user
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

    if command == "/status":
        send_inline_message(
            chat_id=chat_id,

            text=status_text(
                telegram_user_id,
                language,
            ),

            reply_markup=(
                back_to_menu_keyboard(
                    language
                )
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

    if command == "/vip":
        send_inline_message(
            chat_id=chat_id,

            text=vip_text(
                language
            ),

            reply_markup=(
                back_to_menu_keyboard(
                    language
                )
            ),
        )

        print(
            "USER BOT | "
            "VIP | "
            f"UserDB="
            f"{user['id']}",
            flush=True,
        )

        return

    if command == "/channel":
        send_inline_message(
            chat_id=chat_id,

            text=channel_text(
                language
            ),

            reply_markup=(
                channel_keyboard(
                    language
                )
            ),
        )

        return

    if command == "/about":
        send_inline_message(
            chat_id=chat_id,

            text=about_text(
                language
            ),

            reply_markup=(
                back_to_menu_keyboard(
                    language
                )
            ),
        )

        return

    if command == "/help":
        send_inline_message(
            chat_id=chat_id,

            text=help_text(
                language
            ),

            reply_markup=(
                back_to_menu_keyboard(
                    language
                )
            ),
        )

        return

    send_inline_message(
        chat_id=chat_id,

        text=unknown_text(
            language
        ),

        reply_markup=(
            main_menu_keyboard(
                language
            )
        ),
    )


# ============================================================
# CALLBACK HANDLER
# ============================================================

def process_callback_query(
    callback_query
):
    callback_query_id = (
        callback_query.get(
            "id"
        )
    )

    telegram_user = (
        callback_query.get(
            "from"
        )
        or {}
    )

    message = (
        callback_query.get(
            "message"
        )
        or {}
    )

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
        answer_callback_query(
            callback_query_id
        )

        return

    chat_id = (
        chat.get(
            "id"
        )
    )

    message_id = (
        message.get(
            "message_id"
        )
    )

    telegram_user_id = (
        telegram_user.get(
            "id"
        )
    )

    if (
        chat_id is None
        or message_id is None
        or telegram_user_id is None
    ):
        answer_callback_query(
            callback_query_id
        )

        return

    user = register_telegram_user(
        telegram_user
    )

    language = get_language(
        telegram_user
    )

    data = (
        callback_query.get(
            "data"
        )
        or ""
    )

    answer_callback_query(
        callback_query_id
    )

    if data == CALLBACK_MENU:
        edit_inline_message(
            chat_id=chat_id,

            message_id=message_id,

            text=main_menu_text(
                language
            ),

            reply_markup=(
                main_menu_keyboard(
                    language
                )
            ),
        )

        print(
            "USER BOT | "
            "MENU | "
            f"UserDB="
            f"{user['id'] if user else 'n/a'}",
            flush=True,
        )

        return

    if data == CALLBACK_STATUS:
        edit_inline_message(
            chat_id=chat_id,

            message_id=message_id,

            text=status_text(
                telegram_user_id,
                language,
            ),

            reply_markup=(
                back_to_menu_keyboard(
                    language
                )
            ),
        )

        print(
            "USER BOT | "
            "STATUS | "
            f"UserDB="
            f"{user['id'] if user else 'n/a'}",
            flush=True,
        )

        return

    if data == CALLBACK_VIP:
        edit_inline_message(
            chat_id=chat_id,

            message_id=message_id,

            text=vip_text(
                language
            ),

            reply_markup=(
                back_to_menu_keyboard(
                    language
                )
            ),
        )

        print(
            "USER BOT | "
            "VIP | "
            f"UserDB="
            f"{user['id'] if user else 'n/a'}",
            flush=True,
        )

        return

    if data == CALLBACK_ABOUT:
        edit_inline_message(
            chat_id=chat_id,

            message_id=message_id,

            text=about_text(
                language
            ),

            reply_markup=(
                back_to_menu_keyboard(
                    language
                )
            ),
        )

        return

    if data == CALLBACK_HELP:
        edit_inline_message(
            chat_id=chat_id,

            message_id=message_id,

            text=help_text(
                language
            ),

            reply_markup=(
                back_to_menu_keyboard(
                    language
                )
            ),
        )

        return

    edit_inline_message(
        chat_id=chat_id,

        message_id=message_id,

        text=main_menu_text(
            language
        ),

        reply_markup=(
            main_menu_keyboard(
                language
            )
        ),
    )


# ============================================================
# UPDATE HANDLER
# ============================================================

def process_update(
    update
):
    callback_query = (
        update.get(
            "callback_query"
        )
    )

    if callback_query is not None:
        process_callback_query(
            callback_query
        )

        return

    message = (
        update.get(
            "message"
        )
    )

    if message is not None:
        process_private_message(
            message
        )


# ============================================================
# POLLING
# ============================================================

def fetch_updates(
    offset=None,
):
    payload = {
        "timeout":
            POLL_TIMEOUT_SECONDS,

        "allowed_updates":
            [
                "message",
                "callback_query",
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
            result = fetch_updates(
                offset=offset
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
                f"{type(error).__name__}",
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
