import os
import sqlite3
from datetime import datetime, timezone

import requests


BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

FREE_CHANNEL_ID = os.getenv(
    "TELEGRAM_FREE_CHANNEL_ID"
)

DB_PATH = os.getenv(
    "DB_PATH",
    "/app/data/trading.db",
)

TRADE_MODEL_VERSION = "V2"

REPORT_HOUR_UTC = 23
REPORT_MINUTE_UTC = 55


def get_connection():
    connection = sqlite3.connect(
        DB_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    return connection


def init_daily_report_table():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            daily_report_log (
                report_date TEXT PRIMARY KEY,
                sent_at TEXT NOT NULL
            )
            """
        )

        connection.commit()


def report_already_sent(
    report_date
):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT report_date
            FROM daily_report_log
            WHERE report_date = ?
            LIMIT 1
            """,
            (
                report_date,
            ),
        ).fetchone()

    return row is not None


def mark_report_sent(
    report_date
):
    sent_at = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    with get_connection() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO
            daily_report_log (
                report_date,
                sent_at
            )
            VALUES (?, ?)
            """,
            (
                report_date,
                sent_at,
            ),
        )

        connection.commit()


def get_daily_statistics(
    report_date
):
    date_prefix = (
        f"{report_date}%"
    )

    with get_connection() as connection:
        trades = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(
                    CASE
                        WHEN exit_reason = 'TAKE_PROFIT'
                        THEN 1
                        ELSE 0
                    END
                ) AS take_profits,
                SUM(
                    CASE
                        WHEN exit_reason = 'STOP_LOSS'
                        THEN 1
                        ELSE 0
                    END
                ) AS stop_losses,
                SUM(
                    CASE
                        WHEN exit_reason = 'TIMEOUT'
                        THEN 1
                        ELSE 0
                    END
                ) AS timeouts,
                SUM(
                    CASE
                        WHEN status = 'AMBIGUOUS'
                        THEN 1
                        ELSE 0
                    END
                ) AS ambiguous,
                SUM(
                    COALESCE(
                        net_pnl_pips,
                        0
                    )
                ) AS net_pips,
                AVG(
                    r_multiple
                ) AS avg_r
            FROM virtual_trades
            WHERE model_version = ?
              AND status != 'OPEN'
              AND exit_candle_time LIKE ?
            """,
            (
                TRADE_MODEL_VERSION,
                date_prefix,
            ),
        ).fetchone()

        signals = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM signal_events
            WHERE candle_time LIKE ?
            """,
            (
                date_prefix,
            ),
        ).fetchone()

        open_trades = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM virtual_trades
            WHERE model_version = ?
              AND status = 'OPEN'
            """,
            (
                TRADE_MODEL_VERSION,
            ),
        ).fetchone()

    return {
        "trades": (
            trades["total"]
            or 0
        ),
        "take_profits": (
            trades["take_profits"]
            or 0
        ),
        "stop_losses": (
            trades["stop_losses"]
            or 0
        ),
        "timeouts": (
            trades["timeouts"]
            or 0
        ),
        "ambiguous": (
            trades["ambiguous"]
            or 0
        ),
        "net_pips": (
            trades["net_pips"]
            or 0.0
        ),
        "avg_r": (
            trades["avg_r"]
            or 0.0
        ),
        "signals": (
            signals["total"]
            or 0
        ),
        "open_trades": (
            open_trades["total"]
            or 0
        ),
    }


def format_report_date(
    report_date
):
    parsed = datetime.strptime(
        report_date,
        "%Y-%m-%d",
    )

    return parsed.strftime(
        "%d %b %Y"
    )


def build_daily_report(
    report_date,
    stats,
):
    formatted_date = (
        format_report_date(
            report_date
        )
    )

    text = (
        "📊 <b>AS · DAILY REPORT</b>\n"
        "\n"
        f"📅 <b>{formatted_date}</b>\n"
        "\n"
        f"Completed trades: "
        f"<b>{stats['trades']}</b>\n"
        f"✅ Take Profit: "
        f"<b>{stats['take_profits']}</b>\n"
        f"❌ Stop Loss: "
        f"<b>{stats['stop_losses']}</b>\n"
        f"⏱ Time Exit: "
        f"<b>{stats['timeouts']}</b>\n"
    )

    if stats["ambiguous"] > 0:
        text += (
            f"⚠️ Ambiguous: "
            f"<b>{stats['ambiguous']}</b>\n"
        )

    text += (
        "\n"
        f"💰 Net result: "
        f"<b>{stats['net_pips']:+.2f} "
        f"pips</b>\n"
        f"📐 Average R: "
        f"<b>{stats['avg_r']:+.2f}R</b>\n"
        "\n"
        f"🔎 Signals detected: "
        f"<b>{stats['signals']}</b>\n"
        f"🔓 Open trades now: "
        f"<b>{stats['open_trades']}</b>\n"
        "\n"
        "🕒 <b>All times UTC</b>\n"
        "\n"
        "<i>Transparent V2 simulated "
        "trade statistics.</i>\n"
        "\n"
        "<b>AS | Forex & Crypto</b>\n"
        "@ASForexCrypto"
    )

    return text


def send_free_channel_message(
    text
):
    if (
        not BOT_TOKEN
        or not FREE_CHANNEL_ID
    ):
        print(
            "DAILY REPORT WARNING: "
            "Telegram token or free "
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
                "chat_id": FREE_CHANNEL_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )

        data = response.json()

        if not data.get("ok"):
            print(
                "DAILY REPORT "
                f"TELEGRAM ERROR: {data}",
                flush=True,
            )

            return False

        return True

    except Exception as error:
        print(
            "DAILY REPORT ERROR: "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

        return False


def send_daily_report_if_due():
    init_daily_report_table()

    now = datetime.now(
        timezone.utc
    )

    if (
        now.hour < REPORT_HOUR_UTC
    ):
        return False

    if (
        now.hour
        == REPORT_HOUR_UTC
        and now.minute
        < REPORT_MINUTE_UTC
    ):
        return False

    report_date = (
        now.strftime(
            "%Y-%m-%d"
        )
    )

    if report_already_sent(
        report_date
    ):
        return False

    stats = get_daily_statistics(
        report_date
    )

    text = build_daily_report(
        report_date,
        stats,
    )

    sent = (
        send_free_channel_message(
            text
        )
    )

    if sent:
        mark_report_sent(
            report_date
        )

        print(
            "DAILY REPORT SENT | "
            f"Date={report_date} | "
            f"Trades={stats['trades']} | "
            f"NetPips="
            f"{stats['net_pips']:+.2f}",
            flush=True,
        )

    return sent
