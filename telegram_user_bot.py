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

from vip_access import (
    init_vip_access_table,
    create_vip_invite,
    process_vip_join_request,
    sync_expired_vip_access,
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

VIP_SYNC_INTERVAL_SECONDS = 60

STATE_KEY = "last_update_id"

CALLBACK_MENU = "MENU"
CALLBACK_STATUS = "STATUS"
CALLBACK_VIP = "VIP"
CALLBACK_VIP_ACCESS = "VIP_ACCESS"
CALLBACK_CHANNEL = "CHANNEL"
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

    connection.row_factory = sqlite3.Row

    return connection


def init_user_bot_state_table():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            telegram_user_bot_state (
                state_key TEXT PRIMARY KEY,
                state_value TEXT NOT NULL
            )
            """
        )

        connection.commit()


def get_last_update_id():
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT state_value
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
            row["state_value"]
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
            INSERT INTO telegram_user_bot_state (
                state_key,
                state_value
            )
            VALUES (?, ?)

            ON CONFLICT (state_key)
            DO UPDATE SET
                state_value = excluded.state_value
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

    return result is not None


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
        payload["reply_markup"] = (
            reply_markup
        )

    return telegram_request(
        "sendMessage",
        payload,
    )


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
        payload["reply_markup"] = (
            reply_markup
        )

    result = telegram_request(
        "editMessageText",
        payload,
    )

    return result is not None


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

    if value.startswith("@"):
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
        status_button = (
            "📊 Мой статус"
        )

        vip_button = (
            "⭐ VIP"
        )

        channel_button = (
            "📢 Бесплатный канал"
        )

        about_button = (
            "ℹ️ О проекте"
        )

        help_button = (
            "❓ Помощь"
        )

    else:
        status_button = (
            "📊 My Status"
        )

        vip_button = (
            "⭐ VIP"
        )

        channel_button = (
            "📢 Free Channel"
        )

        about_button = (
            "ℹ️ About"
        )

        help_button = (
            "❓ Help"
        )

    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        status_button,

                    "callback_data":
                        CALLBACK_STATUS,
                }
            ],
            [
                {
                    "text":
                        vip_button,

                    "callback_data":
                        CALLBACK_VIP,
                }
            ],
            [
                {
                    "text":
                        channel_button,

                    "callback_data":
                        CALLBACK_CHANNEL,
                }
            ],
            [
                {
                    "text":
                        about_button,

                    "callback_data":
                        CALLBACK_ABOUT,
                }
            ],
            [
                {
                    "text":
                        help_button,

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


def vip_keyboard(
    telegram_user_id,
    language,
):
    access = get_effective_access(
        telegram_user_id
    )

    buttons = []

    is_active_vip = (
        access is not None
        and access.get(
            "plan"
        ) == "VIP"
        and access.get(
            "status"
        ) == "ACTIVE"
    )

    if is_active_vip:
        if language == "ru":
            access_text = (
                "🔐 Получить доступ в VIP"
            )

        else:
            access_text = (
                "🔐 Get VIP Access"
            )

        buttons.append(
            [
                {
                    "text":
                        access_text,

                    "callback_data":
                        CALLBACK_VIP_ACCESS,
                }
            ]
        )

    if language == "ru":
        back_text = (
            "🏠 Главное меню"
        )

    else:
        back_text = (
            "🏠 Main Menu"
        )

    buttons.append(
        [
            {
                "text":
                    back_text,

                "callback_data":
                    CALLBACK_MENU,
            }
        ]
    )

    return {
        "inline_keyboard":
            buttons
    }


def vip_invite_keyboard(
    language,
    invite_link,
):
    if language == "ru":
        open_text = (
            "🔐 Войти в VIP"
        )

        back_text = (
            "🏠 Главное меню"
        )

    else:
        open_text = (
            "🔐 Join VIP"
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
                        invite_link,
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
        first_name or ""
    )

    safe_plan = html.escape(
        plan or "FREE"
    )

    if language == "ru":
        if safe_name:
            greeting = (
                f"Привет, {safe_name}!"
            )

        else:
            greeting = "Привет!"

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
        greeting = "Hello!"

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
    access = get_effective_access(
        telegram_user_id
    )

    if access is None:
        if language == "ru":
            return (
                "Аккаунт ещё не зарегистрирован."
            )

        return (
            "Your account is not "
            "registered yet."
        )

    plan = (
        access.get(
            "plan"
        )
        or "FREE"
    )

    status = (
        access.get(
            "status"
        )
        or "ACTIVE"
    )

    expires_at = access.get(
        "expires_at"
    )

    if language == "ru":
        if (
            plan == "VIP"
            and status == "ACTIVE"
        ):
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

    if (
        plan == "VIP"
        and status == "ACTIVE"
    ):
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
    telegram_user_id,
    language,
):
    access = get_effective_access(
        telegram_user_id
    )

    is_vip = (
        access is not None
        and access.get(
            "plan"
        ) == "VIP"
        and access.get(
            "status"
        ) == "ACTIVE"
    )

    if language == "ru":
        if is_vip:
            expires_at = (
                access.get(
                    "expires_at"
                )
                or "Без ограничения"
            )

            return (
                "⭐ <b>AS VIP</b>\n"
                "\n"
                "✅ Ваша VIP-подписка активна.\n"
                "\n"
                "Действует до: "
                f"<b>{html.escape(str(expires_at))} UTC</b>\n"
                "\n"
                "Нажмите кнопку ниже, "
                "чтобы получить персональный "
                "доступ в VIP-канал."
            )

        return (
            "⭐ <b>AS VIP</b>\n"
            "\n"
            "VIP предоставляет доступ "
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
            "<b>недоступна</b>."
        )

    if is_vip:
        expires_at = (
            access.get(
                "expires_at"
            )
            or "No expiration"
        )

        return (
            "⭐ <b>AS VIP</b>\n"
            "\n"
            "✅ Your VIP subscription "
            "is active.\n"
            "\n"
            "Expires: "
            f"<b>{html.escape(str(expires_at))} UTC</b>\n"
            "\n"
            "Press the button below "
            "to get personal access "
            "to the VIP channel."
        )

    return (
        "⭐ <b>AS VIP</b>\n"
        "\n"
        "VIP provides access "
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
        "<b>not available for purchase yet</b>."
    )


def vip_invite_text(
    language,
    expires_at,
):
    safe_expires = html.escape(
        str(
            expires_at
        )
    )

    if language == "ru":
        return (
            "🔐 <b>Персональный VIP-доступ</b>\n"
            "\n"
            "Ссылка создана специально "
            "для вашего Telegram-аккаунта.\n"
            "\n"
            "⏳ Ссылка действует до:\n"
            f"<b>{safe_expires} UTC</b>\n"
            "\n"
            "После нажатия Telegram "
            "отправит запрос на вступление.\n"
            "Бот автоматически проверит "
            "ваш Telegram ID и одобрит доступ.\n"
            "\n"
            "⚠️ Не передавайте эту ссылку "
            "другим людям."
        )

    return (
        "🔐 <b>Personal VIP Access</b>\n"
        "\n"
        "This link was created specifically "
        "for your Telegram account.\n"
        "\n"
        "⏳ Link valid until:\n"
        f"<b>{safe_expires} UTC</b>\n"
        "\n"
        "After you press Join VIP, "
        "Telegram will send a join request.\n"
        "The bot will verify your Telegram ID "
        "and approve access automatically.\n"
        "\n"
        "⚠️ Do not share this link "
        "with other people."
    )


def vip_error_text(
    language
):
    if language == "ru":
        return (
            "⚠️ <b>Не удалось создать доступ</b>\n"
            "\n"
            "Проверьте, что VIP-подписка "
            "активна, и попробуйте ещё раз."
        )

    return (
        "⚠️ <b>Could not create VIP access</b>\n"
        "\n"
        "Check that your VIP subscription "
        "is active and try again."
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
            "• никаких обещаний "
            "гарантированной прибыли\n"
            "• сначала тестирование, "
            "потом запуск\n"
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
            "VIP и доступ в канал\n"
            "\n"
            "📢 <b>Бесплатный канал</b> — "
            "публичный Telegram-канал\n"
            "\n"
            "ℹ️ <b>О проекте</b> — "
            "информация об AS"
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
        "VIP and channel access\n"
        "\n"
        "📢 <b>Free Channel</b> — "
        "public Telegram channel\n"
        "\n"
        "ℹ️ <b>About</b> — "
        "information about AS"
    )


def unknown_text(
    language
):
    if language == "ru":
        return (
            "Я не распознал эту команду.\n"
            "\n"
            "Используйте главное меню."
        )

    return (
        "I don't recognize that command.\n"
        "\n"
        "Please use the main menu."
    )


# ============================================================
# USER
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
# START SCREEN
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

    access = get_effective_access(
        telegram_user_id
    )

    plan = (
        access.get(
            "plan"
        )
        if access
        else "FREE"
    )

    first_result = telegram_request(
        "sendMessage",
        {
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

            "reply_markup": {
                "remove_keyboard":
                    True,
            },
        },
    )

    if first_result is None:
        return False

    message = (
        first_result.get(
            "result"
        )
        or {}
    )

    message_id = message.get(
        "message_id"
    )

    if message_id is None:
        return True

    result = telegram_request(
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

    return result is not None


# ============================================================
# COMMAND MENU
# ============================================================

def set_bot_commands():
    english_commands = [
        {
            "command": "start",
            "description": "Open AS bot",
        },
        {
            "command": "status",
            "description": "My subscription",
        },
        {
            "command": "vip",
            "description": "VIP information",
        },
        {
            "command": "channel",
            "description": "Free channel",
        },
        {
            "command": "about",
            "description": "About AS",
        },
        {
            "command": "help",
            "description": "Help",
        },
    ]

    russian_commands = [
        {
            "command": "start",
            "description": "Открыть бота AS",
        },
        {
            "command": "status",
            "description": "Моя подписка",
        },
        {
            "command": "vip",
            "description": "Информация о VIP",
        },
        {
            "command": "channel",
            "description": "Бесплатный канал",
        },
        {
            "command": "about",
            "description": "О проекте AS",
        },
        {
            "command": "help",
            "description": "Помощь",
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
# COMMAND HANDLER
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

    if chat.get(
        "type"
    ) != "private":
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

    chat_id = chat.get(
        "id"
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
            chat_id,
            telegram_user,
        )

        access = get_effective_access(
            telegram_user_id
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
            f"UserDB={user['id']} | "
            f"Plan={plan}",
            flush=True,
        )

        return

    if command == "/status":
        send_inline_message(
            chat_id,
            status_text(
                telegram_user_id,
                language,
            ),
            back_to_menu_keyboard(
                language
            ),
        )

        print(
            "USER BOT | "
            "STATUS | "
            f"UserDB={user['id']}",
            flush=True,
        )

        return

    if command == "/vip":
        send_inline_message(
            chat_id,
            vip_text(
                telegram_user_id,
                language,
            ),
            vip_keyboard(
                telegram_user_id,
                language,
            ),
        )

        print(
            "USER BOT | "
            "VIP | "
            f"UserDB={user['id']}",
            flush=True,
        )

        return

    if command == "/channel":
        send_inline_message(
            chat_id,
            channel_text(
                language
            ),
            channel_keyboard(
                language
            ),
        )

        return

    if command == "/about":
        send_inline_message(
            chat_id,
            about_text(
                language
            ),
            back_to_menu_keyboard(
                language
            ),
        )

        return

    if command == "/help":
        send_inline_message(
            chat_id,
            help_text(
                language
            ),
            back_to_menu_keyboard(
                language
            ),
        )

        return

    send_inline_message(
        chat_id,
        unknown_text(
            language
        ),
        main_menu_keyboard(
            language
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

    chat_id = chat.get(
        "id"
    )

    message_id = message.get(
        "message_id"
    )

    telegram_user_id = (
        telegram_user.get(
            "id"
        )
    )

    answer_callback_query(
        callback_query_id
    )

    if (
        chat_id is None
        or message_id is None
        or telegram_user_id is None
    ):
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

    if data == CALLBACK_MENU:
        edit_inline_message(
            chat_id,
            message_id,
            main_menu_text(
                language
            ),
            main_menu_keyboard(
                language
            ),
        )

        print(
            "USER BOT | MENU | "
            f"UserDB="
            f"{user['id'] if user else 'n/a'}",
            flush=True,
        )

        return

    if data == CALLBACK_STATUS:
        edit_inline_message(
            chat_id,
            message_id,
            status_text(
                telegram_user_id,
                language,
            ),
            back_to_menu_keyboard(
                language
            ),
        )

        print(
            "USER BOT | STATUS | "
            f"UserDB="
            f"{user['id'] if user else 'n/a'}",
            flush=True,
        )

        return

    if data == CALLBACK_VIP:
        edit_inline_message(
            chat_id,
            message_id,
            vip_text(
                telegram_user_id,
                language,
            ),
            vip_keyboard(
                telegram_user_id,
                language,
            ),
        )

        print(
            "USER BOT | VIP | "
            f"UserDB="
            f"{user['id'] if user else 'n/a'}",
            flush=True,
        )

        return

    if data == CALLBACK_VIP_ACCESS:
        result = create_vip_invite(
            telegram_user_id
        )

        if (
            result
            and result.get(
                "ok"
            )
        ):
            invite_link = result[
                "invite_link"
            ]

            expires_at = result[
                "expires_at"
            ]

            edit_inline_message(
                chat_id,
                message_id,
                vip_invite_text(
                    language,
                    expires_at,
                ),
                vip_invite_keyboard(
                    language,
                    invite_link,
                ),
            )

            print(
                "USER BOT | "
                "VIP INVITE | "
                f"UserDB="
                f"{user['id'] if user else 'n/a'}",
                flush=True,
            )

        else:
            edit_inline_message(
                chat_id,
                message_id,
                vip_error_text(
                    language
                ),
                back_to_menu_keyboard(
                    language
                ),
            )

        return

    if data == CALLBACK_CHANNEL:
        edit_inline_message(
            chat_id,
            message_id,
            channel_text(
                language
            ),
            channel_keyboard(
                language
            ),
        )

        return

    if data == CALLBACK_ABOUT:
        edit_inline_message(
            chat_id,
            message_id,
            about_text(
                language
            ),
            back_to_menu_keyboard(
                language
            ),
        )

        return

    if data == CALLBACK_HELP:
        edit_inline_message(
            chat_id,
            message_id,
            help_text(
                language
            ),
            back_to_menu_keyboard(
                language
            ),
        )

        return


# ============================================================
# UPDATE HANDLER
# ============================================================

def process_update(
    update
):
    join_request = update.get(
        "chat_join_request"
    )

    if join_request is not None:
        process_vip_join_request(
            join_request
        )

        return

    callback_query = update.get(
        "callback_query"
    )

    if callback_query is not None:
        process_callback_query(
            callback_query
        )

        return

    message = update.get(
        "message"
    )

    if message is not None:
        process_private_message(
            message
        )


# ============================================================
# VIP SUBSCRIPTION SYNC
# ============================================================

def run_vip_access_sync():
    try:
        return sync_expired_vip_access()

    except Exception as error:
        print(
            "VIP ACCESS SYNC ERROR | "
            f"{type(error).__name__}",
            flush=True,
        )

        return None


# ============================================================
# POLLING
# ============================================================

def fetch_updates(
    offset=None,
):
    payload = {
        "timeout":
            POLL_TIMEOUT_SECONDS,

        "allowed_updates": [
            "message",
            "callback_query",
            "chat_join_request",
        ],
    }

    if offset is not None:
        payload["offset"] = (
            offset
        )

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

    init_vip_access_table()

    print(
        "VIP access: database ready",
        flush=True,
    )

    print(
        "VIP access sync: every 60s",
        flush=True,
    )

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

    last_vip_sync = 0.0

    print(
        "User bot: polling enabled",
        flush=True,
    )

    while True:
        try:
            current_monotonic = (
                time.monotonic()
            )

            if (
                current_monotonic
                - last_vip_sync
                >= VIP_SYNC_INTERVAL_SECONDS
            ):
                run_vip_access_sync()

                last_vip_sync = (
                    current_monotonic
                )

            result = fetch_updates(
                offset
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
                        f"{type(error).__name__}",
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
