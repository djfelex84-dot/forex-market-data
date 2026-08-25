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


# Weekly report becomes due
# after the previous week
# has completely finished.
#
# Monday 00:10 UTC.
REPORT_WEEKDAY = 0
REPORT_HOUR_UTC = 0
REPORT_MINUTE_UTC = 10

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


def init_weekly_report_table():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            weekly_report_log (
                week_start TEXT
                PRIMARY KEY,

                sent_at TEXT
                NOT NULL
            )
            """
        )

        connection.commit()


def report_already_processed(
    week_start
):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT week_start

            FROM weekly_report_log

            WHERE week_start = ?

            LIMIT 1
            """,
            (
                week_start,
            ),
        ).fetchone()

    return row is not None


def mark_report_processed(
    week_start
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
            weekly_report_log (
                week_start,
                sent_at
            )

            VALUES (?, ?)
            """,
            (
                week_start,
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
        "decided_trades": 0,
        "win_rate": 0.0,
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


def get_previous_completed_week(
    now
):
    # Start of current week:
    # Monday 00:00 UTC.
    current_week_start = (
        datetime(
            year=now.year,
            month=now.month,
            day=now.day,
            tzinfo=timezone.utc,
        )
        - timedelta(
            days=now.weekday()
        )
    )

    previous_week_start = (
        current_week_start
        - timedelta(days=7)
    )

    previous_week_end = (
        current_week_start
    )

    return (
        previous_week_start,
        previous_week_end,
    )


def get_weekly_statistics(
    week_start,
    week_end,
):
    stats_by_symbol = {
        symbol: empty_symbol_stats()
        for symbol in SYMBOLS
    }

    # Candle timestamps store the
    # candle OPEN time.
    #
    # Read a slightly wider window,
    # then classify by effective
    # candle CLOSE time.
    raw_start = (
        week_start
        - timedelta(days=1)
    ).strftime(
        TIME_FORMAT
    )

    raw_end = (
        week_end
        + timedelta(days=1)
    ).strftime(
        TIME_FORMAT
    )

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
              AND exit_candle_time
                  IS NOT NULL
              AND exit_candle_time >= ?
              AND exit_candle_time < ?
            """,
            (
                TRADE_MODEL_VERSION,
                raw_start,
                raw_end,
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
                raw_start,
                raw_end,
            ),
        ).fetchall()

    # =========================
    # CLOSED TRADES
    # =========================

    for trade in trades:
        closed_at = effective_time(
            trade[
                "exit_candle_time"
            ],
            trade["interval"],
        ).replace(
            tzinfo=timezone.utc
        )

        if not (
            week_start
            <= closed_at
            < week_end
        ):
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

    # =========================
    # SIGNALS
    # =========================

    for signal in signals:
        signal_at = effective_time(
            signal["candle_time"],
            signal["interval"],
        ).replace(
            tzinfo=timezone.utc
        )

        if not (
            week_start
            <= signal_at
            < week_end
        ):
            continue

        stats = ensure_symbol(
            stats_by_symbol,
            signal["symbol"],
        )

        stats["signals"] += 1

    # =========================
    # SYMBOL CALCULATIONS
    # =========================

    for stats in (
        stats_by_symbol.values()
    ):
        if stats["r_count"] > 0:
            stats["avg_r"] = (
                stats["total_r"]
                / stats["r_count"]
            )

        stats["decided_trades"] = (
            stats["take_profits"]
            + stats["stop_losses"]
        )

        if (
            stats["decided_trades"]
            > 0
        ):
            stats["win_rate"] = (
                stats["take_profits"]
                / stats["decided_trades"]
                * 100
            )

    # =========================
    # ALL MARKETS
    # =========================

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

    total["decided_trades"] = (
        total["take_profits"]
        + total["stop_losses"]
    )

    if (
        total["decided_trades"]
        > 0
    ):
        total["win_rate"] = (
            total["take_profits"]
            / total["decided_trades"]
            * 100
        )

    return {
        "total": total,
        "symbols": stats_by_symbol,
    }


def has_weekly_activity(
    stats
):
    total = stats["total"]

    return (
        total["trades"] > 0
        or total["signals"] > 0
    )


def format_week_range(
    week_start,
    week_end,
):
    last_day = (
        week_end
        - timedelta(days=1)
    )

    if (
        week_start.month
        == last_day.month
    ):
        return (
            f"{week_start.strftime('%d')}–"
            f"{last_day.strftime('%d %b %Y')}"
        )

    return (
        f"{week_start.strftime('%d %b')}–"
        f"{last_day.strftime('%d %b %Y')}"
    )


def build_weekly_report(
    week_start,
    week_end,
    stats,
):
    week_range = (
        format_week_range(
            week_start,
            week_end,
        )
    )

    total = stats["total"]

    lines = [
        "📊 <b>AS · WEEKLY REPORT</b>",
        "",
        f"📅 <b>{week_range}</b>",
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
                "🎯 Win rate: "
                f"<b>{total['win_rate']:.1f}%</b>"
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
                    "Win rate: "
                    f"<b>{symbol_stats['win_rate']:.1f}%</b>"
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
                "Mon 00:00 – Sun 23:59 UTC</b>"
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
            "WEEKLY REPORT WARNING: "
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
                "WEEKLY REPORT "
                f"TELEGRAM ERROR: {data}",
                flush=True,
            )

            return False

        return True

    except Exception as error:
        print(
            "WEEKLY REPORT ERROR: "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

        return False


def send_weekly_report_if_due():
    init_weekly_report_table()

    now = datetime.now(
        timezone.utc
    )

    (
        week_start,
        week_end,
    ) = get_previous_completed_week(
        now
    )

    # Do not publish the previous
    # week until Monday 00:10 UTC.
    current_week_start = (
        week_end
    )

    due_time = (
        current_week_start
        + timedelta(
            hours=REPORT_HOUR_UTC,
            minutes=REPORT_MINUTE_UTC,
        )
    )

    if now < due_time:
        return False

    week_start_key = (
        week_start.strftime(
            "%Y-%m-%d"
        )
    )

    if report_already_processed(
        week_start_key
    ):
        return False

    stats = get_weekly_statistics(
        week_start,
        week_end,
    )

    if not has_weekly_activity(
        stats
    ):
        mark_report_processed(
            week_start_key
        )

        print(
            "WEEKLY REPORT SKIPPED | "
            f"Week={week_start_key} | "
            "No activity",
            flush=True,
        )

        return False

    text = build_weekly_report(
        week_start,
        week_end,
        stats,
    )

    sent = (
        send_free_channel_message(
            text
        )
    )

    if sent:
        mark_report_processed(
            week_start_key
        )

        print(
            "WEEKLY REPORT SENT | "
            f"Week={week_start_key} | "
            f"Trades="
            f"{stats['total']['trades']} | "
            f"Signals="
            f"{stats['total']['signals']} | "
            f"WinRate="
            f"{stats['total']['win_rate']:.1f}% | "
            f"TotalR="
            f"{stats['total']['total_r']:+.2f}R",
            flush=True,
        )

    return sent
