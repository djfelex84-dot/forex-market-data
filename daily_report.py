import os
import sqlite3

from datetime import (
    datetime,
    timedelta,
    timezone,
)

import requests

from config import (
    SYMBOLS,
    TRADE_MODEL_VERSION,
)


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


REPORT_HOUR_UTC = 0
REPORT_MINUTE_UTC = 5

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_connection():
    connection = sqlite3.connect(
        DB_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    return connection


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


def effective_time(
    candle_time,
    interval,
):
    candle_open = datetime.strptime(
        candle_time,
        TIME_FORMAT,
    )

    return (
        candle_open
        + timedelta(
            minutes=interval_minutes(
                interval
            )
        )
    )


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


def report_already_processed(
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


def mark_report_processed(
    report_date
):
    processed_at = datetime.now(
        timezone.utc
    ).strftime(
        TIME_FORMAT
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
                processed_at,
            ),
        )

        connection.commit()


def empty_symbol_stats():
    return {
        "trades": 0,
        "take_profits": 0,
        "stop_losses": 0,
        "timeouts": 0,
        "ambiguous": 0,
        "net_pips": 0.0,
        "total_r": 0.0,
        "r_count": 0,
        "avg_r": 0.0,
        "signals": 0,
    }


def ensure_symbol(
    stats_by_symbol,
    symbol,
):
    if symbol not in stats_by_symbol:
        stats_by_symbol[
            symbol
        ] = empty_symbol_stats()

    return stats_by_symbol[
        symbol
    ]


def get_daily_statistics(
    report_date
):
    report_day = datetime.strptime(
        report_date,
        "%Y-%m-%d",
    ).date()

    window_start = (
        datetime.combine(
            report_day,
            datetime.min.time(),
        )
        - timedelta(days=1)
    ).strftime(
        TIME_FORMAT
    )

    window_end = (
        datetime.combine(
            report_day,
            datetime.min.time(),
        )
        + timedelta(days=1)
    ).strftime(
        TIME_FORMAT
    )

    stats_by_symbol = {
        symbol: empty_symbol_stats()
        for symbol in SYMBOLS
    }

    with get_connection() as connection:
        trades = connection.execute(
            """
            SELECT
                symbol,
                interval,
                status,
                exit_candle_time,
                exit_reason,
                net_pnl_pips,
                r_multiple

            FROM virtual_trades

            WHERE model_version = ?
              AND status != 'OPEN'
              AND exit_candle_time IS NOT NULL
              AND exit_candle_time >= ?
              AND exit_candle_time < ?
            """,
            (
                TRADE_MODEL_VERSION,
                window_start,
                window_end,
            ),
        ).fetchall()

        signals = connection.execute(
            """
            SELECT
                symbol,
                interval,
                candle_time

            FROM signal_events

            WHERE candle_time >= ?
              AND candle_time < ?
            """,
            (
                window_start,
                window_end,
            ),
        ).fetchall()

    for trade in trades:
        closed_at = effective_time(
            trade[
                "exit_candle_time"
            ],
            trade["interval"],
        )

        if closed_at.date() != report_day:
            continue

        stats = ensure_symbol(
            stats_by_symbol,
            trade["symbol"],
        )

        stats["trades"] += 1

        if (
            trade["exit_reason"]
            == "TAKE_PROFIT"
        ):
            stats[
                "take_profits"
            ] += 1

        elif (
            trade["exit_reason"]
            == "STOP_LOSS"
        ):
            stats[
                "stop_losses"
            ] += 1

        elif (
            trade["exit_reason"]
            == "TIMEOUT"
        ):
            stats[
                "timeouts"
            ] += 1

        if (
            trade["status"]
            == "AMBIGUOUS"
        ):
            stats[
                "ambiguous"
            ] += 1

        if (
            trade["net_pnl_pips"]
            is not None
        ):
            stats["net_pips"] += float(
                trade[
                    "net_pnl_pips"
                ]
            )

        if (
            trade["r_multiple"]
            is not None
        ):
            stats["total_r"] += float(
                trade[
                    "r_multiple"
                ]
            )

            stats["r_count"] += 1

    for signal in signals:
        signal_at = effective_time(
            signal["candle_time"],
            signal["interval"],
        )

        if signal_at.date() != report_day:
            continue

        stats = ensure_symbol(
            stats_by_symbol,
            signal["symbol"],
        )

        stats["signals"] += 1

    for stats in (
        stats_by_symbol.values()
    ):
        if stats["r_count"] > 0:
            stats["avg_r"] = (
                stats["total_r"]
                / stats["r_count"]
            )

    total = empty_symbol_stats()

    for stats in (
        stats_by_symbol.values()
    ):
        total["trades"] += (
            stats["trades"]
        )

        total[
            "take_profits"
        ] += stats[
            "take_profits"
        ]

        total[
            "stop_losses"
        ] += stats[
            "stop_losses"
        ]

        total["timeouts"] += (
            stats["timeouts"]
        )

        total["ambiguous"] += (
            stats["ambiguous"]
        )

        total["total_r"] += (
            stats["total_r"]
        )

        total["r_count"] += (
            stats["r_count"]
        )

        total["signals"] += (
            stats["signals"]
        )

    if total["r_count"] > 0:
        total["avg_r"] = (
            total["total_r"]
            / total["r_count"]
        )

    return {
        "total": total,
        "symbols": stats_by_symbol,
    }


def has_daily_activity(
    stats
):
    total = stats["total"]

    return (
        total["trades"] > 0
        or total["signals"] > 0
    )


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

    total = stats["total"]

    lines = [
        "📊 <b>AS · DAILY REPORT</b>",
        "",
        f"📅 <b>{formatted_date}</b>",
        "",
        "🌐 <b>ALL MARKETS</b>",
        (
            "Completed trades: "
            f"<b>{total['trades']}</b>"
        ),
        (
            "✅ Take Profit: "
            f"<b>{total['take_profits']}</b>"
        ),
        (
            "❌ Stop Loss: "
            f"<b>{total['stop_losses']}</b>"
        ),
        (
            "⏱ Time Exit: "
            f"<b>{total['timeouts']}</b>"
        ),
    ]

    if total["ambiguous"] > 0:
        lines.append(
            "⚠️ Ambiguous: "
            f"<b>{total['ambiguous']}</b>"
        )

    lines.extend(
        [
            (
                "📈 Total result: "
                f"<b>{total['total_r']:+.2f}R</b>"
            ),
            (
                "📐 Average R: "
                f"<b>{total['avg_r']:+.2f}R</b>"
            ),
            (
                "🔎 Signals detected: "
                f"<b>{total['signals']}</b>"
            ),
        ]
    )

    for symbol in SYMBOLS:
        symbol_stats = stats[
            "symbols"
        ].get(
            symbol,
            empty_symbol_stats(),
        )

        if (
            symbol_stats["trades"] == 0
            and symbol_stats["signals"] == 0
        ):
            continue

        lines.extend(
            [
                "",
                f"💱 <b>{symbol}</b>",
                (
                    "Closed: "
                    f"<b>{symbol_stats['trades']}</b>"
                    " · "
                    "TP: "
                    f"<b>{symbol_stats['take_profits']}</b>"
                    " · "
                    "SL: "
                    f"<b>{symbol_stats['stop_losses']}</b>"
                ),
            ]
        )

        if (
            symbol_stats["timeouts"] > 0
        ):
            lines.append(
                "Time Exit: "
                f"<b>{symbol_stats['timeouts']}</b>"
            )

        if (
            symbol_stats["ambiguous"] > 0
        ):
            lines.append(
                "Ambiguous: "
                f"<b>{symbol_stats['ambiguous']}</b>"
            )

        lines.extend(
            [
                (
                    "Net: "
                    f"<b>{symbol_stats['net_pips']:+.2f} "
                    "pips</b>"
                    " · "
                    "R: "
                    f"<b>{symbol_stats['total_r']:+.2f}R</b>"
                ),
                (
                    "Signals: "
                    f"<b>{symbol_stats['signals']}</b>"
                ),
            ]
        )

    lines.extend(
        [
            "",
            (
                "🕒 <b>Report period: "
                "00:00–23:59 UTC</b>"
            ),
            "",
            (
                "<i>Transparent V2 simulated "
                "trade statistics.</i>"
            ),
            "",
            "<b>AS | Forex & Crypto</b>",
            "@ASForexCrypto",
        ]
    )

    return "\n".join(
        lines
    )


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
                "chat_id":
                    FREE_CHANNEL_ID,

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

    if now.hour < REPORT_HOUR_UTC:
        return False

    if (
        now.hour
        == REPORT_HOUR_UTC
        and now.minute
        < REPORT_MINUTE_UTC
    ):
        return False

    report_date = (
        now.date()
        - timedelta(days=1)
    ).strftime(
        "%Y-%m-%d"
    )

    if report_already_processed(
        report_date
    ):
        return False

    stats = get_daily_statistics(
        report_date
    )

    if not has_daily_activity(
        stats
    ):
        mark_report_processed(
            report_date
        )

        print(
            "DAILY REPORT SKIPPED | "
            f"Date={report_date} | "
            "No activity",
            flush=True,
        )

        return False

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
        mark_report_processed(
            report_date
        )

        print(
            "DAILY REPORT SENT | "
            f"Date={report_date} | "
            f"Trades="
            f"{stats['total']['trades']} | "
            f"Signals="
            f"{stats['total']['signals']} | "
            f"TotalR="
            f"{stats['total']['total_r']:+.2f}R",
            flush=True,
        )

    return sent
