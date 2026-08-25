import os
import sqlite3

from datetime import (
    datetime,
    timedelta,
)

import requests

from config import (
    INTERVAL,
)

from trade_chart import (
    create_trade_chart,
)


BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

CHANNEL_ID = os.getenv(
    "TELEGRAM_CHANNEL_ID"
)

VIP_CHANNEL_ID = os.getenv(
    "TELEGRAM_VIP_CHANNEL_ID"
)

DB_PATH = os.getenv(
    "DB_PATH",
    "/app/data/trading.db",
)


SEND_MESSAGE_URL = (
    f"https://api.telegram.org/"
    f"bot{BOT_TOKEN}/sendMessage"
    if BOT_TOKEN
    else None
)

SEND_PHOTO_URL = (
    f"https://api.telegram.org/"
    f"bot{BOT_TOKEN}/sendPhoto"
    if BOT_TOKEN
    else None
)


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


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


# ============================================================
# INTERVAL
# ============================================================

def interval_minutes(
    interval
):
    if not interval:
        interval = INTERVAL

    if interval.endswith(
        "min"
    ):
        return int(
            interval.replace(
                "min",
                "",
            )
        )

    if interval.endswith(
        "h"
    ):
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


# ============================================================
# TELEGRAM SEND
# ============================================================

def send_message_to_channel(
    channel_id,
    text,
):
    if (
        not BOT_TOKEN
        or not channel_id
    ):
        print(
            "TELEGRAM WARNING | "
            "token or channel ID "
            "is missing",
            flush=True,
        )

        return False

    try:
        response = requests.post(
            SEND_MESSAGE_URL,
            json={
                "chat_id":
                    channel_id,

                "text":
                    text,

                "parse_mode":
                    "HTML",

                "disable_web_page_preview":
                    True,
            },
            timeout=15,
        )

    except requests.exceptions.RequestException as error:
        print(
            "TELEGRAM MESSAGE ERROR | "
            f"{channel_id} | "
            f"{type(error).__name__}",
            flush=True,
        )

        return False

    except Exception as error:
        print(
            "TELEGRAM MESSAGE ERROR | "
            f"{channel_id} | "
            f"{type(error).__name__}",
            flush=True,
        )

        return False

    if response.status_code >= 400:
        print(
            "TELEGRAM MESSAGE ERROR | "
            f"{channel_id} | "
            f"HTTP "
            f"{response.status_code} "
            f"{response.reason}",
            flush=True,
        )

        return False

    return True


def send_photo_to_channel(
    channel_id,
    caption,
    image_buffer,
):
    if (
        not BOT_TOKEN
        or not channel_id
    ):
        print(
            "TELEGRAM WARNING | "
            "token or channel ID "
            "is missing",
            flush=True,
        )

        return False

    if image_buffer is None:
        return False

    try:
        image_buffer.seek(
            0
        )

        response = requests.post(
            SEND_PHOTO_URL,
            data={
                "chat_id":
                    channel_id,

                "caption":
                    caption,

                "parse_mode":
                    "HTML",
            },
            files={
                "photo": (
                    "trade_signal.png",
                    image_buffer,
                    "image/png",
                )
            },
            timeout=30,
        )

    except requests.exceptions.RequestException as error:
        print(
            "TELEGRAM PHOTO ERROR | "
            f"{channel_id} | "
            f"{type(error).__name__}",
            flush=True,
        )

        return False

    except Exception as error:
        print(
            "TELEGRAM PHOTO ERROR | "
            f"{channel_id} | "
            f"{type(error).__name__}",
            flush=True,
        )

        return False

    if response.status_code >= 400:
        print(
            "TELEGRAM PHOTO ERROR | "
            f"{channel_id} | "
            f"HTTP "
            f"{response.status_code} "
            f"{response.reason}",
            flush=True,
        )

        return False

    return True


# ============================================================
# SIMPLE SEND HELPERS
# ============================================================

def send_message(
    text,
):
    return send_message_to_channel(
        CHANNEL_ID,
        text,
    )


def send_vip_message(
    text,
):
    return send_message_to_channel(
        VIP_CHANNEL_ID,
        text,
    )


def send_photo(
    caption,
    image_buffer,
):
    return send_photo_to_channel(
        CHANNEL_ID,
        caption,
        image_buffer,
    )


def send_vip_photo(
    caption,
    image_buffer,
):
    return send_photo_to_channel(
        VIP_CHANNEL_ID,
        caption,
        image_buffer,
    )


# ============================================================
# DISPLAY HELPERS
# ============================================================

def direction_icon(
    signal
):
    if signal == "BUY":
        return "📈"

    if signal == "SELL":
        return "📉"

    return "📊"


def get_signal_time(
    trade
):
    candle_time = trade.get(
        "entry_candle_time"
    )

    if not candle_time:
        return "n/a"

    interval = (
        trade.get(
            "interval"
        )
        or INTERVAL
    )

    try:
        candle_open = (
            datetime.strptime(
                candle_time,
                TIME_FORMAT,
            )
        )

        confirmed_time = (
            candle_open
            + timedelta(
                minutes=(
                    interval_minutes(
                        interval
                    )
                )
            )
        )

        return confirmed_time.strftime(
            "%Y-%m-%d %H:%M UTC"
        )

    except Exception:
        return (
            f"{candle_time} UTC"
        )


def get_result_confirmed_time(
    trade
):
    candle_time = trade.get(
        "candle_time"
    )

    if not candle_time:
        candle_time = trade.get(
            "exit_candle_time"
        )

    if not candle_time:
        return "n/a"

    interval = (
        trade.get(
            "interval"
        )
        or INTERVAL
    )

    try:
        candle_open = (
            datetime.strptime(
                candle_time,
                TIME_FORMAT,
            )
        )

        confirmed_time = (
            candle_open
            + timedelta(
                minutes=(
                    interval_minutes(
                        interval
                    )
                )
            )
        )

        return confirmed_time.strftime(
            "%Y-%m-%d %H:%M UTC"
        )

    except Exception:
        return (
            f"{candle_time} UTC"
        )


def get_max_trade_time_text(
    trade
):
    minutes = trade.get(
        "max_hold_minutes"
    )

    if minutes is None:
        return "n/a"

    try:
        minutes = int(
            minutes
        )

    except (
        TypeError,
        ValueError,
    ):
        return str(
            minutes
        )

    if (
        minutes > 0
        and minutes % 60 == 0
    ):
        hours = (
            minutes
            // 60
        )

        if hours == 1:
            return "1 hour"

        return (
            f"{hours} hours"
        )

    return (
        f"{minutes} minutes"
    )


# ============================================================
# ORIGINAL TRADE LOOKUP
# ============================================================

def get_original_trade(
    trade_id
):
    if trade_id is None:
        return None

    try:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    signal_event_id,
                    entry_candle_time,
                    symbol,
                    interval,
                    signal,
                    entry_price,
                    stop_loss,
                    take_profit,
                    risk_pips,
                    reward_pips,
                    spread_pips,
                    max_hold_minutes,
                    status,
                    exit_candle_time,
                    exit_price,
                    exit_reason,
                    net_pnl_pips,
                    r_multiple

                FROM virtual_trades

                WHERE id = ?

                LIMIT 1
                """,
                (
                    int(
                        trade_id
                    ),
                ),
            ).fetchone()

        if row is None:
            return None

        return dict(
            row
        )

    except Exception as error:
        print(
            "TELEGRAM TRADE LOOKUP ERROR | "
            f"TradeID={trade_id} | "
            f"{type(error).__name__}",
            flush=True,
        )

        return None


# ============================================================
# SIGNAL ID
# ============================================================

def clean_symbol_for_id(
    symbol
):
    value = (
        symbol
        or "UNKNOWN"
    ).upper()

    return "".join(
        character
        for character in value
        if character.isalnum()
    )


def get_signal_id(
    trade
):
    if trade is None:
        return "AS-UNKNOWN"

    symbol = clean_symbol_for_id(
        trade.get(
            "symbol"
        )
    )

    signal_event_id = (
        trade.get(
            "signal_event_id"
        )
    )

    if signal_event_id is not None:
        return (
            f"AS-{symbol}-"
            f"{signal_event_id}"
        )

    trade_id = (
        trade.get(
            "id"
        )
        or trade.get(
            "trade_id"
        )
    )

    if trade_id is not None:
        return (
            f"AS-{symbol}-T"
            f"{trade_id}"
        )

    candle_time = trade.get(
        "entry_candle_time"
    )

    if candle_time:
        try:
            candle_open = (
                datetime.strptime(
                    candle_time,
                    TIME_FORMAT,
                )
            )

            interval = (
                trade.get(
                    "interval"
                )
                or INTERVAL
            )

            signal_time = (
                candle_open
                + timedelta(
                    minutes=(
                        interval_minutes(
                            interval
                        )
                    )
                )
            )

            return (
                f"AS-{symbol}-"
                f"{signal_time.strftime('%Y%m%d-%H%M')}"
            )

        except Exception:
            pass

    return (
        f"AS-{symbol}-UNKNOWN"
    )


# ============================================================
# OPEN TRADE TEXT
# ============================================================

def build_trade_opened_text(
    trade,
    test_mode=False,
):
    signal = trade[
        "signal"
    ]

    icon = direction_icon(
        signal
    )

    risk_pips = float(
        trade[
            "risk_pips"
        ]
    )

    reward_pips = float(
        trade[
            "reward_pips"
        ]
    )

    reward_ratio = (
        reward_pips
        / risk_pips
        if risk_pips
        else 0
    )

    signal_time = (
        get_signal_time(
            trade
        )
    )

    interval = (
        trade.get(
            "interval"
        )
        or INTERVAL
    )

    max_trade_time = (
        get_max_trade_time_text(
            trade
        )
    )

    signal_id = get_signal_id(
        trade
    )

    text = (
        f"{icon} "
        f"<b>{trade['symbol']} · "
        f"{signal}</b>\n"
        "\n"
        "✅ <b>SIGNAL ACTIVE</b>\n"
        "🆔 Signal ID: "
        f"<code>{signal_id}</code>\n"
        "\n"
        "🎯 Entry: "
        f"<code>{trade['entry']:.5f}</code>\n"
        "🛑 Stop Loss: "
        f"<code>{trade['stop_loss']:.5f}</code>\n"
        "🏁 Take Profit: "
        f"<code>{trade['take_profit']:.5f}</code>\n"
        "\n"
        "⚖️ Risk: "
        f"<b>{risk_pips:.1f} pips</b>\n"
        "🏆 Reward: "
        f"<b>{reward_pips:.1f} pips</b>\n"
        "📊 R:R: "
        f"<b>1:{reward_ratio:.2f}</b>\n"
        "⏱ Timeframe: "
        f"<b>{interval}</b>\n"
        "⌛ Max trade time: "
        f"<b>{max_trade_time}</b>\n"
        "\n"
        "🕒 Signal time: "
        f"<b>{signal_time}</b>\n"
        "\n"
        "
