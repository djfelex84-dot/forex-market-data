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

        response.raise_for_status()

        return True

    except Exception as error:
        print(
            "TELEGRAM MESSAGE ERROR | "
            f"{channel_id} | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

        return False


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
                "photo":
                    (
                        "trade_signal.png",
                        image_buffer,
                        "image/png",
                    )
            },
            timeout=30,
        )

        response.raise_for_status()

        return True

    except Exception as error:
        print(
            "TELEGRAM PHOTO ERROR | "
            f"{channel_id} | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

        return False


def send_message(
    text
):
    return send_message_to_channel(
        CHANNEL_ID,
        text,
    )


def send_vip_message(
    text
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

    interval = trade.get(
        "interval"
    ) or INTERVAL

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
        return "n/a"

    interval = trade.get(
        "interval"
    ) or INTERVAL

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
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

        return None


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

    text = (
        f"{icon} "
        f"<b>{trade['symbol']} · "
        f"{signal}</b>\n"
        "\n"
        "✅ <b>SIGNAL ACTIVE</b>\n"
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
        "💰 Reward: "
        f"<b>{reward_pips:.1f} pips</b>\n"
        "📐 R:R: "
        f"<b>1:{reward_ratio:.2f}</b>\n"
        "⏱ Timeframe: "
        f"<b>{interval}</b>\n"
        "⌛ Max trade time: "
        f"<b>{max_trade_time}</b>\n"
        "\n"
        "🕒 Signal time: "
        f"<b>{signal_time}</b>"
    )

    if test_mode:
        text += (
            "\n\n"
            "<i>Test signal · "
            "simulated execution</i>"
        )

    return text


def result_header(
    result
):
    if result == "TAKE_PROFIT":
        return (
            "🏁 <b>TAKE PROFIT</b>"
        )

    if result == "STOP_LOSS":
        return (
            "🛑 <b>STOP LOSS</b>"
        )

    if result == "TIMEOUT":
        return (
            "⏱ <b>TIME EXIT</b>"
        )

    if result == "AMBIGUOUS":
        return (
            "⚠️ <b>AMBIGUOUS RESULT</b>"
        )

    return (
        f"📊 <b>{result}</b>"
    )


def result_pnl_icon(
    net_pips
):
    if net_pips is None:
        return "⚪"

    if float(
        net_pips
    ) > 0:
        return "🟢"

    if float(
        net_pips
    ) < 0:
        return "🔴"

    return "⚪"


def build_trade_closed_text(
    trade,
    test_mode=False,
):
    trade_id = trade.get(
        "trade_id"
    )

    original = (
        get_original_trade(
            trade_id
        )
    )

    symbol = trade.get(
        "symbol"
    )

    signal = trade.get(
        "signal"
    )

    interval = (
        trade.get(
            "interval"
        )
        or INTERVAL
    )

    entry_price = None
    signal_time = "n/a"

    if original is not None:
        symbol = (
            original.get(
                "symbol"
            )
            or symbol
        )

        signal = (
            original.get(
                "signal"
            )
            or signal
        )

        interval = (
            original.get(
                "interval"
            )
            or interval
        )

        entry_price = (
            original.get(
                "entry_price"
            )
        )

        signal_time = (
            get_signal_time(
                original
            )
        )

    if symbol is None:
        symbol = "UNKNOWN"

    if signal is None:
        signal = "UNKNOWN"

    result = trade.get(
        "result",
        "UNKNOWN",
    )

    result_confirmed = (
        get_result_confirmed_time(
            {
                **trade,
                "interval":
                    interval,
            }
        )
    )

    icon = direction_icon(
        signal
    )

    text = (
        f"{result_header(result)}\n"
        "\n"
        f"{icon} "
        f"<b>{symbol} · "
        f"{signal}</b>\n"
    )

    if entry_price is not None:
        text += (
            "\n"
            "🎯 Entry: "
            f"<code>{float(entry_price):.5f}</code>\n"
            "⏱ Timeframe: "
            f"<b>{interval}</b>\n"
            "🕒 Signal time: "
            f"<b>{signal_time}</b>\n"
        )

    if result == "AMBIGUOUS":
        text += (
            "\n"
            "⚠️ Both Stop Loss and "
            "Take Profit were touched "
            "inside the same candle.\n"
            "\n"
            "The exact order cannot be "
            "determined from OHLC data."
        )

    else:
        net_pips = trade.get(
            "net_pips"
        )

        r_value = trade.get(
            "r"
        )

        pnl_icon = (
            result_pnl_icon(
                net_pips
            )
        )

        if net_pips is not None:
            text += (
                "\n"
                f"{pnl_icon} "
                "Net result: "
                f"<b>{float(net_pips):+.2f} "
                "pips</b>"
            )

        if r_value is not None:
            text += (
                "\n"
                "📊 Result: "
                f"<b>{float(r_value):+.2f}R</b>"
            )

    text += (
        "\n\n"
        "🕒 Result confirmed: "
        f"<b>{result_confirmed}</b>"
    )

    if (
        test_mode
        and trade_id is not None
    ):
        text += (
            "\n"
            "🧪 Trade ID: "
            f"<code>{trade_id}</code>"
        )

    if test_mode:
        text += (
            "\n\n"
            "<i>Test signal · "
            "simulated execution</i>"
        )

    return text


def send_trade_opened(
    trade,
    candles,
):
    test_text = (
        build_trade_opened_text(
            trade,
            test_mode=True,
        )
    )

    vip_text = (
        build_trade_opened_text(
            trade,
            test_mode=False,
        )
    )

    image_buffer = None

    try:
        image_buffer = (
            create_trade_chart(
                candles=candles,
                trade=trade,
                symbol=trade[
                    "symbol"
                ],
            )
        )

    except Exception as error:
        print(
            "TRADE CHART ERROR | "
            f"{trade['symbol']} | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

    if image_buffer is not None:
        test_sent = (
            send_photo_to_channel(
                CHANNEL_ID,
                test_text,
                image_buffer,
            )
        )

    else:
        test_sent = (
            send_message_to_channel(
                CHANNEL_ID,
                test_text,
            )
        )

    if test_sent:
        print(
            "SIGNAL SENT | "
            f"{trade['symbol']} | "
            "TEST",
            flush=True,
        )

    if VIP_CHANNEL_ID:
        if image_buffer is not None:
            vip_sent = (
                send_photo_to_channel(
                    VIP_CHANNEL_ID,
                    vip_text,
                    image_buffer,
                )
            )

        else:
            vip_sent = (
                send_message_to_channel(
                    VIP_CHANNEL_ID,
                    vip_text,
                )
            )

        if vip_sent:
            print(
                "SIGNAL SENT | "
                f"{trade['symbol']} | "
                "VIP",
                flush=True,
            )

    return test_sent


def send_trade_closed(
    trade
):
    test_text = (
        build_trade_closed_text(
            trade,
            test_mode=True,
        )
    )

    vip_text = (
        build_trade_closed_text(
            trade,
            test_mode=False,
        )
    )

    test_sent = (
        send_message_to_channel(
            CHANNEL_ID,
            test_text,
        )
    )

    if test_sent:
        print(
            "RESULT SENT | "
            f"{trade['symbol']} | "
            "TEST",
            flush=True,
        )

    if VIP_CHANNEL_ID:
        vip_sent = (
            send_message_to_channel(
                VIP_CHANNEL_ID,
                vip_text,
            )
        )

        if vip_sent:
            print(
                "RESULT SENT | "
                f"{trade['symbol']} | "
                "VIP",
                flush=True,
            )

    return test_sent
