import os
from datetime import datetime, timezone

import requests

from config import SYMBOL


BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

API_URL = (
    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    if BOT_TOKEN
    else None
)


def send_message(text):
    if not BOT_TOKEN or not CHANNEL_ID:
        print(
            "TELEGRAM WARNING: token or channel ID is missing",
            flush=True,
        )
        return False

    try:
        response = requests.post(
            API_URL,
            json={
                "chat_id": CHANNEL_ID,
                "text": text,
            },
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
            f"{type(error).__name__}: {error}",
            flush=True,
        )
        return False


def send_trade_opened(trade):
    now = datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")

    text = (
        "🔔 NEW SIGNAL\n\n"
        f"Pair: {SYMBOL}\n"
        f"Direction: {trade['signal']}\n"
        f"Entry: {trade['entry']:.5f}\n"
        f"Stop Loss: {trade['stop_loss']:.5f}\n"
        f"Take Profit: {trade['take_profit']:.5f}\n"
        f"Risk: {trade['risk_pips']:.2f} pips\n"
        f"Reward: {trade['reward_pips']:.2f} pips\n"
        f"R:R: 1:"
        f"{trade['reward_pips'] / trade['risk_pips']:.2f}\n"
        f"Spread model: {trade['spread_pips']:.2f} pips\n"
        f"Max hold: {trade['max_hold_minutes']} min\n"
        f"Published: {now}\n\n"
        "Test signal — simulated trade. "
        "Profit is not guaranteed."
    )

    return send_message(text)


def send_trade_closed(trade):
    now = datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")

    result = trade["result"]

    if result == "TAKE_PROFIT":
        icon = "✅"

    elif result == "STOP_LOSS":
        icon = "❌"

    elif result == "TIMEOUT":
        icon = "⏱"

    else:
        icon = "⚠️"

    if result == "AMBIGUOUS":
        text = (
            f"{icon} TRADE RESULT\n\n"
            f"Pair: {SYMBOL}\n"
            f"Direction: {trade['signal']}\n"
            "Result: AMBIGUOUS\n"
            "SL and TP were touched "
            "in the same 5-minute candle.\n"
            f"Candle: {trade['candle_time']} UTC\n"
            f"Published: {now}"
        )

    else:
        text = (
            f"{icon} TRADE CLOSED\n\n"
            f"Pair: {SYMBOL}\n"
            f"Direction: {trade['signal']}\n"
            f"Result: {result}\n"
            f"Gross: {trade['gross_pips']:+.2f} pips\n"
            f"Net: {trade['net_pips']:+.2f} pips\n"
            f"R: {trade['r']:+.2f}R\n"
            f"Candle: {trade['candle_time']} UTC\n"
            f"Published: {now}"
        )

    return send_message(text)
