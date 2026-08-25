import os
from datetime import datetime, timedelta

import requests

from config import SYMBOL, INTERVAL


BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

CHANNEL_ID = os.getenv(
    "TELEGRAM_CHANNEL_ID"
)

API_URL = (
    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
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
        f"Unsupported interval: {interval}"
    )


def send_message(
    text,
    reply_markup=None,
):
    if not BOT_TOKEN or not CHANNEL_ID:
        print(
            "TELEGRAM WARNING: "
            "token or channel ID is missing",
            flush=True,
        )
        return False

    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        response = requests.post(
            API_URL,
            json=payload,
            timeout=10,
        )

        data = response.json()

        if not data.get("ok"):
            print(
                f"TELEGRAM ERROR: {data}",
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


def get_direction_icon(signal):
    if signal == "BUY":
        return "📈"

    if signal == "SELL":
        return "📉"

    return "📊"


def get_signal_time(trade):
    candle_time = datetime.strptime(
        trade["entry_candle_time"],
        TIME_FORMAT,
    )

    minutes = interval_minutes(
        trade["interval"]
    )

    signal_time = (
        candle_time
        + timedelta(
            minutes=minutes
        )
    )

    return signal_time.strftime(
        "%Y-%m-%d %H:%M UTC"
    )


def format_max_trade_time(minutes):
    if minutes % 60 == 0:
        hours = minutes // 60

        if hours == 1:
            return "1 hour"

        return f"{hours} hours"

    return f"{minutes} min"


def send_trade_opened(trade):
    direction_icon = get_direction_icon(
        trade["signal"]
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
        trade["reward_pips"]
        / trade["risk_pips"]
    )

    signal_time = get_signal_time(
        trade
    )

    max_trade_time = format_max_trade_time(
        trade["max_hold_minutes"]
    )

    text = (
        f"{direction_icon} "
        f"<b>{SYMBOL} · {trade['signal']}</b>\n"
        "\n"
        "✅ <b>SIGNAL ACTIVE</b>\n"
        "\n"
        f"🎯 Entry: <code>{entry}</code>\n"
        f"🛑 Stop Loss: <code>{stop_loss}</code>\n"
        f"🏁 Take Profit: <code>{take_profit}</code>\n"
        "\n"
        f"⚖️ Risk: "
        f"<b>{trade['risk_pips']:.1f} pips</b>\n"
        f"💰 Reward: "
        f"<b>{trade['reward_pips']:.1f} pips</b>\n"
        f"📐 R:R: "
        f"<b>1:{rr:.2f}</b>\n"
        f"⏱ Timeframe: "
        f"<b>{INTERVAL}</b>\n"
        f"⌛ Max trade time: "
        f"<b>{max_trade_time}</b>\n"
        "\n"
        f"🕒 Signal time: "
        f"<b>{signal_time}</b>\n"
        "\n"
        "<i>Test signal · simulated execution</i>"
    )

    buttons = {
        "inline_keyboard": [
            [
                {
                    "text": "📋 Entry",
                    "copy_text": {
                        "text": entry
                    },
                },
                {
                    "text": "📋 SL",
                    "copy_text": {
                        "text": stop_loss
                    },
                },
                {
                    "text": "📋 TP",
                    "copy_text": {
                        "text": take_profit
                    },
                },
            ]
        ]
    }

    return send_message(
        text=text,
        reply_markup=buttons,
    )


def send_trade_closed(trade):
    result = trade["result"]

    direction_icon = get_direction_icon(
        trade["signal"]
    )

    result_candle = (
        f"{trade['candle_time']} UTC"
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
            f"<b>{SYMBOL} · {trade['signal']}</b>\n"
            "\n"
            "SL and TP were reached inside "
            "the same 5-minute candle.\n"
            "\n"
            "The exact order cannot be determined "
            "from OHLC data.\n"
            "\n"
            f"🕒 Result candle: "
            f"<b>{result_candle}</b>\n"
            "\n"
            "<i>Test signal · simulated execution</i>"
        )

    else:
        net_pips = trade["net_pips"]
        r_value = trade["r"]

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
            f"<b>{SYMBOL} · {trade['signal']}</b>\n"
            "\n"
            f"{pnl_icon} Net result: "
            f"<b>{net_pips:+.2f} pips</b>\n"
            f"📊 Result: "
            f"<b>{r_value:+.2f}R</b>\n"
            "\n"
            f"🕒 Result candle: "
            f"<b>{result_candle}</b>\n"
            "\n"
            "<i>Test signal · simulated execution</i>"
        )

    return send_message(
        text=text
    )
