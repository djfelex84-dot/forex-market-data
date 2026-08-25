import os
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


def interval_minutes(interval):
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


def send_message_to_channel(
    channel_id,
    text,
):
    if not BOT_TOKEN or not channel_id:
        print(
            "TELEGRAM WARNING: "
            "token or channel ID is missing",
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

        data = response.json()

        if not data.get("ok"):
            print(
                f"TELEGRAM ERROR: "
                f"{data}",
                flush=True,
            )
            return False

        return True

    except Exception as error:
        print(
            f"TELEGRAM ERROR: "
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
    if not BOT_TOKEN or not channel_id:
        print(
            "TELEGRAM WARNING: "
            "token or channel ID is missing",
            flush=True,
        )
        return False

    try:
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

        data = response.json()

        if not data.get("ok"):
            print(
                f"TELEGRAM ERROR: "
                f"{data}",
                flush=True,
            )
            return False

        return True

    except Exception as error:
        print(
            f"TELEGRAM ERROR: "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )
        return False


def send_message(text):
    return send_message_to_channel(
        CHANNEL_ID,
        text,
    )


def send_vip_message(text):
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


def get_direction_icon(signal):
    if signal == "BUY":
        return "📈"

    if signal == "SELL":
        return "📉"

    return "📊"


def get_signal_time(trade):
    candle_time = datetime.strptime(
        trade[
            "entry_candle_time"
        ],
        TIME_FORMAT,
    )

    signal_time = (
        candle_time
        + timedelta(
            minutes=(
                interval_minutes(
                    trade[
                        "interval"
                    ]
                )
            )
        )
    )

    return signal_time.strftime(
        "%Y-%m-%d %H:%M UTC"
    )


def get_result_confirmed_time(
    trade
):
    candle_open = datetime.strptime(
        trade[
            "candle_time"
        ],
        TIME_FORMAT,
    )

    trade_interval = trade.get(
        "interval",
        INTERVAL,
    )

    confirmed_time = (
        candle_open
        + timedelta(
            minutes=(
                interval_minutes(
                    trade_interval
                )
            )
        )
    )

    return confirmed_time.strftime(
        "%Y-%m-%d %H:%M UTC"
    )


def format_max_trade_time(minutes):
    if minutes % 60 == 0:
        hours = minutes // 60

        if hours == 1:
            return "1 hour"

        return f"{hours} hours"

    return f"{minutes} min"


def build_trade_opened_text(
    trade
):
    direction_icon = (
        get_direction_icon(
            trade[
                "signal"
            ]
        )
    )

    entry = (
        f"{trade['entry']:.5f}"
    )

    stop_loss = (
        f"{trade['stop_loss']:.5f}"
    )

    take_profit = (
        f"{trade['take_profit']:.5f}"
    )

    rr = (
        trade[
            "reward_pips"
        ]
        / trade[
            "risk_pips"
        ]
    )

    signal_time = (
        get_signal_time(
            trade
        )
    )

    max_trade_time = (
        format_max_trade_time(
            trade[
                "max_hold_minutes"
            ]
        )
    )

    return (
        f"{direction_icon} "
        f"<b>{trade['symbol']} · "
        f"{trade['signal']}</b>\n"
        "\n"
        "✅ <b>SIGNAL ACTIVE</b>\n"
        "\n"
        f"🎯 Entry: "
        f"<code>{entry}</code>\n"
        f"🛑 Stop Loss: "
        f"<code>{stop_loss}</code>\n"
        f"🏁 Take Profit: "
        f"<code>{take_profit}</code>\n"
        "\n"
        f"⚖️ Risk: "
        f"<b>{trade['risk_pips']:.1f} "
        f"pips</b>\n"
        f"💰 Reward: "
        f"<b>{trade['reward_pips']:.1f} "
        f"pips</b>\n"
        f"📐 R:R: "
        f"<b>1:{rr:.2f}</b>\n"
        f"⏱ Timeframe: "
        f"<b>{trade['interval']}</b>\n"
        f"⌛ Max trade time: "
        f"<b>{max_trade_time}</b>\n"
        "\n"
        f"🕒 Signal time: "
        f"<b>{signal_time}</b>\n"
        "\n"
        "<i>Test signal · "
        "simulated execution</i>"
    )


def send_trade_opened(
    trade,
    candles,
):
    text = (
        build_trade_opened_text(
            trade
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

        sent = send_photo(
            caption=text,
            image_buffer=image_buffer,
        )

        if sent:
            return True

        print(
            "TELEGRAM CHART WARNING: "
            "photo send failed, "
            "using text fallback",
            flush=True,
        )

    except Exception as error:
        print(
            "TELEGRAM CHART ERROR: "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

    finally:
        if image_buffer:
            image_buffer.close()

    return send_message(
        text
    )


def send_trade_closed(
    trade
):
    result = trade[
        "result"
    ]

    direction_icon = (
        get_direction_icon(
            trade[
                "signal"
            ]
        )
    )

    result_confirmed = (
        get_result_confirmed_time(
            trade
        )
    )

    if result == "TAKE_PROFIT":
        icon = "✅"
        title = "TAKE PROFIT"

    elif result == "STOP_LOSS":
        icon = "❌"
        title = "STOP LOSS"

    elif result == "TIMEOUT":
        icon = "⏱"
        title = "TIME EXIT"

    else:
        icon = "⚠️"
        title = "RESULT UNCLEAR"

    if result == "AMBIGUOUS":
        text = (
            f"{icon} <b>{title}</b>\n"
            "\n"
            f"{direction_icon} "
            f"<b>{trade['symbol']} · "
            f"{trade['signal']}</b>\n"
            "\n"
            "SL and TP were reached "
            "inside the same "
            f"{trade.get('interval', INTERVAL)} "
            "candle.\n"
            "\n"
            "The exact order cannot "
            "be determined from "
            "OHLC data.\n"
            "\n"
            f"🕒 Result confirmed: "
            f"<b>{result_confirmed}</b>\n"
            "\n"
            "<i>Test signal · "
            "simulated execution</i>"
        )

    else:
        net_pips = (
            trade[
                "net_pips"
            ]
        )

        r_value = (
            trade[
                "r"
            ]
        )

        if net_pips > 0:
            pnl_icon = "🟢"

        elif net_pips < 0:
            pnl_icon = "🔴"

        else:
            pnl_icon = "⚪"

        text = (
            f"{icon} <b>{title}</b>\n"
            "\n"
            f"{direction_icon} "
            f"<b>{trade['symbol']} · "
            f"{trade['signal']}</b>\n"
            "\n"
            f"{pnl_icon} Net result: "
            f"<b>{net_pips:+.2f} "
            f"pips</b>\n"
            f"📊 Result: "
            f"<b>{r_value:+.2f}R</b>\n"
            "\n"
            f"🕒 Result confirmed: "
            f"<b>{result_confirmed}</b>\n"
            "\n"
            "<i>Test signal · "
            "simulated execution</i>"
        )

    return send_message(
        text
    )
