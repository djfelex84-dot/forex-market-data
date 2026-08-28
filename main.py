import os
import time

from datetime import (
    datetime,
    timezone,
)

from config import (
    SYMBOLS,
    INTERVAL,
)

from market_data import (
    fetch_candles,
)

from strategy import (
    analyze_market,
)


SIGNAL_QUALITY_AVAILABLE = False
SIGNAL_QUALITY_READY = False
SIGNAL_QUALITY_IMPORT_ERROR = None

try:
    from signal_quality import (
        build_quality_snapshot,
    )

    from signal_quality_storage import (
        init_quality_storage,
        save_quality_snapshot,
    )

    SIGNAL_QUALITY_AVAILABLE = True

except Exception as error:
    SIGNAL_QUALITY_IMPORT_ERROR = (
        f"{type(error).__name__}: "
        f"{error}"
    )


MULTI_TIMEFRAME_AVAILABLE = False
MULTI_TIMEFRAME_IMPORT_ERROR = None

try:
    from multi_timeframe_analysis import (
        build_multi_timeframe_analysis,
    )

    MULTI_TIMEFRAME_AVAILABLE = True

except Exception as error:
    MULTI_TIMEFRAME_IMPORT_ERROR = (
        f"{type(error).__name__}: "
        f"{error}"
    )


from storage import (
    init_db,
    save_analysis,
    count_records,
    count_signal_events,
    create_signal_event_if_new,
    get_outcome_summary,
    count_virtual_trades,
    count_open_virtual_trades,
    get_trade_summary,
    get_excursion_summary,
)

from evaluator import (
    evaluate_pending_signals,
)

from trade_manager import (
    ensure_virtual_trades,
    evaluate_open_trades,
)

from telegram_notifier import (
    send_trade_opened,
    send_trade_closed,
    send_vip_message,
)

from daily_report import (
    init_daily_report_table,
    send_daily_report_if_due,
)

from weekly_report import (
    init_weekly_report_table,
    send_weekly_report_if_due,
)

from market_overview import (
    init_market_overview_table,
    process_market_overview,
)

from economic_calendar import (
    init_economic_calendar,
    process_economic_calendar,
)

from news_digest import (
    init_news_digest_tables,
    process_news_digest,
)

from health_monitor import (
    init_health_monitor_table,
    process_health_monitor,
)

from research_15m import (
    init_15m_research_tables,
    process_15m_research,
)

from research_15m_trades import (
    init_15m_trade_tables,
    process_15m_trade_research,
)

from user_subscriptions import (
    init_user_subscription_tables,
)

from telegram_user_bot import (
    start_user_bot_polling,
)


CANDLE_CLOSE_DELAY_SECONDS = 15

LEGACY_LIVE_CANDLE_LIMIT = 120

VIP_TEST_MARKER_PATH = (
    "/app/data/"
    "vip_connection_test_sent.flag"
)


def interval_to_seconds(interval):
    if interval.endswith("min"):
        return (
            int(
                interval.replace(
                    "min",
                    "",
                )
            )
            * 60
        )

    if interval.endswith("h"):
        return (
            int(
                interval.replace(
                    "h",
                    "",
                )
            )
            * 3600
        )

    raise ValueError(
        f"Unsupported interval: "
        f"{interval}"
    )


INTERVAL_SECONDS = (
    interval_to_seconds(
        INTERVAL
    )
)


def send_vip_connection_test_once():
    if os.path.exists(
        VIP_TEST_MARKER_PATH
    ):
        print(
            "VIP channel: "
            "connection test already confirmed",
            flush=True,
        )

        return True

    message = (
        "✅ <b>AS VIP channel "
        "connected successfully.</b>\n"
        "\n"
        "<i>Private channel "
        "connection test.</i>"
    )

    sent = send_vip_message(
        message
    )

    if not sent:
        print(
            "VIP CHANNEL TEST FAILED | "
            "Will retry after next restart",
            flush=True,
        )

        return False

    try:
        with open(
            VIP_TEST_MARKER_PATH,
            "w",
            encoding="utf-8",
        ) as file:
            file.write(
                datetime.now(
                    timezone.utc
                ).strftime(
                    "%Y-%m-%d %H:%M:%S UTC"
                )
            )

    except Exception as error:
        print(
            "VIP TEST MARKER ERROR | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

    print(
        "VIP CHANNEL TEST SENT | "
        "Connection confirmed",
        flush=True,
    )

    return True


def format_optional_pips(
    value
):
    if value is None:
        return "n/a"

    return f"{float(value):.2f}"


def format_result(
    symbol,
    result,
):
    return (
        f"["
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        f"] "

        f"{symbol} {INTERVAL} | "

        f"Candle="
        f"{result['datetime']} | "

        f"Close="
        f"{result['close']:.5f} | "

        f"EMA20="
        f"{result['ema_fast']:.5f} | "

        f"EMA50="
        f"{result['ema_slow']:.5f} | "

        f"RSI14="
        f"{result['rsi']:.2f} | "

        f"ATR14="
        f"{result['atr']:.5f} | "

        f"EMA-distance="
        f"{result['ema_distance_atr']:.2f} ATR | "

        f"EMA-direction="
        f"{result['ema_direction']} | "

        f"Trend="
        f"{result['trend']} | "

        f"Candidate="
        f"{result['candidate']} | "

        f"Signal="
        f"{result['signal']} | "

        f"Status="
        f"{result['status']} | "

        f"SetupScore="
        f"{result['setup_score']}/100 | "

        f"{result['reason']}"
    )


def print_new_virtual_trades(
    trades,
    candles,
):
    for trade in trades:
        print(
            "VIRTUAL TRADE V2 OPENED | "

            f"{trade['symbol']} | "

            f"ID="
            f"{trade['id']} | "

            f"{trade['signal']} | "

            f"Entry="
            f"{trade['entry']:.5f} | "

            f"SL="
            f"{trade['stop_loss']:.5f} | "

            f"TP="
            f"{trade['take_profit']:.5f} | "

            f"Risk="
            f"{trade['risk_pips']:.2f} pips | "

            f"Reward="
            f"{trade['reward_pips']:.2f} pips | "

            f"Spread="
            f"{trade['spread_pips']:.2f} pips | "

            f"R:R=1:"
            f"{trade['reward_pips'] / trade['risk_pips']:.2f} | "

            f"MaxHold="
            f"{trade['max_hold_minutes']}m",
            flush=True,
        )

        send_trade_opened(
            trade,
            candles,
        )


def print_trade_results(
    results
):
    for trade in results:
        mae = format_optional_pips(
            trade.get(
                "mae_pips"
            )
        )

        mfe = format_optional_pips(
            trade.get(
                "mfe_pips"
            )
        )

        if (
            trade["result"]
            == "AMBIGUOUS"
        ):
            print(
                "VIRTUAL TRADE V2 RESULT | "

                f"{trade['symbol']} | "

                f"ID="
                f"{trade['trade_id']} | "

                f"{trade['signal']} | "

                "AMBIGUOUS | "

                f"MAE="
                f"{mae} pips | "

                f"MFE="
                f"{mfe} pips | "

                f"Candle="
                f"{trade['candle_time']}",
                flush=True,
            )

        else:
            print(
                "VIRTUAL TRADE V2 CLOSED | "

                f"{trade['symbol']} | "

                f"ID="
                f"{trade['trade_id']} | "

                f"{trade['signal']} | "

                f"{trade['result']} | "

                f"Gross="
                f"{trade['gross_pips']:+.2f} pips | "

                f"Net="
                f"{trade['net_pips']:+.2f} pips | "

                f"R="
                f"{trade['r']:+.2f}R | "

                f"MAE="
                f"{mae} pips | "

                f"MFE="
                f"{mfe} pips | "

                f"Candle="
                f"{trade['candle_time']}",
                flush=True,
            )

        send_trade_closed(
            trade
        )


def print_trade_summary(
    symbol,
):
    summary = (
        get_trade_summary(
            symbol=symbol
        )
    )

    total = (
        summary["total"]
        or 0
    )

    if total == 0:
        return

    print(
        f"----- {symbol} "
        f"VIRTUAL TRADE V2 STATISTICS -----",
        flush=True,
    )

    print(
        f"Trades="
        f"{total} | "

        f"TP="
        f"{summary['take_profits'] or 0} | "

        f"SL="
        f"{summary['stop_losses'] or 0} | "

        f"Timeout="
        f"{summary['timeouts'] or 0} | "

        f"Ambiguous="
        f"{summary['ambiguous'] or 0} | "

        f"Open="
        f"{summary['open_trades'] or 0} | "

        f"NetPips="
        f"{summary['total_net_pips'] or 0:+.2f} | "

        f"AvgNet="
        f"{summary['avg_net_pips'] or 0:+.2f} | "

        f"AvgR="
        f"{summary['avg_r'] or 0:+.2f}R",
        flush=True,
    )


def print_excursion_summary(
    symbol,
):
    summary = (
        get_excursion_summary(
            symbol=symbol
        )
    )

    if not summary:
        return

    print(
        f"----- {symbol} "
        f"MAE / MFE RESEARCH -----",
        flush=True,
    )

    for row in summary:
        avg_mae = (
            format_optional_pips(
                row[
                    "avg_mae_pips"
                ]
            )
        )

        avg_mfe = (
            format_optional_pips(
                row[
                    "avg_mfe_pips"
                ]
            )
        )

        max_mae = (
            format_optional_pips(
                row[
                    "max_mae_pips"
                ]
            )
        )

        max_mfe = (
            format_optional_pips(
                row[
                    "max_mfe_pips"
                ]
            )
        )

        print(
            f"{row['exit_reason']} | "

            f"Closed="
            f"{row['total_closed'] or 0} | "

            f"Tracked="
            f"{row['tracked'] or 0} | "

            f"AvgMAE="
            f"{avg_mae} pips | "

            f"AvgMFE="
            f"{avg_mfe} pips | "

            f"MaxMAE="
            f"{max_mae} pips | "

            f"MaxMFE="
            f"{max_mfe} pips",
            flush=True,
        )


def print_outcome_summary(
    symbol,
):
    summary = (
        get_outcome_summary(
            symbol=symbol
        )
    )

    if not summary:
        return

    print(
        f"----- {symbol} "
        f"15/30/60 RESEARCH -----",
        flush=True,
    )

    for row in summary:
        total = row[
            "total"
        ]

        wins = (
            row[
                "wins"
            ]
            or 0
        )

        win_rate = (
            wins
            / total
            * 100
            if total
            else 0
        )

        print(
            f"{row['horizon_minutes']}m | "

            f"Signals="
            f"{total} | "

            f"Wins="
            f"{wins} | "

            f"Losses="
            f"{row['losses'] or 0} | "

            f"Flat="
            f"{row['flat'] or 0} | "

            f"WinRate="
            f"{win_rate:.1f}% | "

            f"AvgPips="
            f"{row['avg_pips'] or 0:.2f}",
            flush=True,
        )


def process_research_15m(
    symbol,
    candles,
):
    try:
        research_result = (
            process_15m_research(
                symbol=symbol,
                five_minute_candles=candles,
            )
        )

        if research_result is None:
            return None

        process_15m_trade_research(
            symbol=symbol,
            analysis_result=(
                research_result
            ),
        )

        return research_result

    except Exception as error:
        print(
            "15M RESEARCH ERROR | "
            f"{symbol} | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

        return None


def initialize_signal_quality():
    global SIGNAL_QUALITY_READY

    if not SIGNAL_QUALITY_AVAILABLE:
        print(
            "SIGNAL QUALITY DISABLED | "
            f"Import error: "
            f"{SIGNAL_QUALITY_IMPORT_ERROR}",
            flush=True,
        )

        return False

    try:
        init_quality_storage()

        SIGNAL_QUALITY_READY = True

        print(
            "Signal quality measurement: ready",
            flush=True,
        )

        return True

    except Exception as error:
        SIGNAL_QUALITY_READY = False

        print(
            "SIGNAL QUALITY INIT ERROR | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

        return False


def process_signal_quality(
    symbol,
    candles,
    result,
    analysis_id,
    created_at,
):
    if (
        not SIGNAL_QUALITY_AVAILABLE
        or not SIGNAL_QUALITY_READY
    ):
        return None

    try:
        snapshot = (
            build_quality_snapshot(
                candles=candles,
                strategy_result=result,
                interval=INTERVAL,
            )
        )

        saved = (
            save_quality_snapshot(
                symbol=symbol,
                snapshot=snapshot,
                analysis_id=analysis_id,
                created_at=created_at,
            )
        )

        if saved:
            print(
                "QUALITY SNAPSHOT SAVED | "
                f"{symbol} | "
                f"Candle={snapshot['datetime']} | "
                f"Direction={snapshot['direction']} | "
                f"Status={snapshot['strategy_status']}",
                flush=True,
            )

        return snapshot

    except Exception as error:
        print(
            "SIGNAL QUALITY ERROR | "
            f"{symbol} | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

        return None


def process_multi_timeframe_quality(
    symbol,
    candles,
    created_at,
):
    if (
        not MULTI_TIMEFRAME_AVAILABLE
        or not SIGNAL_QUALITY_AVAILABLE
        or not SIGNAL_QUALITY_READY
    ):
        return None

    try:
        mtf_result = (
            build_multi_timeframe_analysis(
                candles,
                symbol=symbol,
            )
        )

        if not mtf_result.get(
            "ready"
        ):
            print(
                "MTF MEASUREMENT NOT READY | "
                f"{symbol} | "
                f"M30="
                f"{mtf_result.get('signal_candle_count', 0)} | "
                f"H1="
                f"{mtf_result.get('context_candle_count', 0)} | "
                f"SafeH1="
                f"{mtf_result.get('safe_context_candle_count', 0)} | "
                f"{mtf_result.get('reason', 'unknown')}",
                flush=True,
            )

            return mtf_result

        signal_snapshot = (
            mtf_result[
                "signal_quality"
            ]
        )

        context_snapshot = (
            mtf_result[
                "context_quality"
            ]
        )

        signal_saved = (
            save_quality_snapshot(
                symbol=symbol,
                snapshot=signal_snapshot,
                analysis_id=None,
                created_at=created_at,
            )
        )

        context_saved = (
            save_quality_snapshot(
                symbol=symbol,
                snapshot=context_snapshot,
                analysis_id=None,
                created_at=created_at,
            )
        )

        if signal_saved:
            print(
                "MTF QUALITY SNAPSHOT SAVED | "
                f"{symbol} | "
                "Interval=30min | "
                f"Candle="
                f"{signal_snapshot['datetime']} | "
                f"Signal="
                f"{mtf_result['signal_direction']} | "
                f"Candidate="
                f"{mtf_result['signal_candidate_direction']}",
                flush=True,
            )

        if context_saved:
            print(
                "MTF QUALITY SNAPSHOT SAVED | "
                f"{symbol} | "
                "Interval=60min | "
                f"Candle="
                f"{context_snapshot['datetime']} | "
                f"Signal="
                f"{mtf_result['context_direction']} | "
                f"Candidate="
                f"{mtf_result['context_candidate_direction']}",
                flush=True,
            )

        if (
            signal_saved
            or context_saved
        ):
            print(
                "MTF ALIGNMENT | "
                f"{symbol} | "
                f"M30="
                f"{mtf_result['signal_direction']} | "
                f"H1="
                f"{mtf_result['context_direction']} | "
                f"Alignment="
                f"{mtf_result['direction_alignment']}",
                flush=True,
            )

        return mtf_result

    except Exception as error:
        print(
            "MTF MEASUREMENT ERROR | "
            f"{symbol} | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

        return None


def analyze_symbol(
    symbol,
):
    all_candles = (
        fetch_candles(
            symbol
        )
    )

    candles = (
        all_candles[
            -LEGACY_LIVE_CANDLE_LIMIT:
        ]
    )

    result = (
        analyze_market(
            candles,
            symbol,
        )
    )

    created_at = (
        datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    print(
        format_result(
            symbol,
            result,
        ),
        flush=True,
    )

    saved, analysis_id = (
        save_analysis(
            created_at=created_at,
            symbol=symbol,
            interval=INTERVAL,
            result=result,
        )
    )

    if saved:
        print(
            f"{symbol} | "
            f"New candle saved | "
            f"Records="
            f"{count_records(symbol)}",
            flush=True,
        )

    else:
        print(
            f"{symbol} | "
            f"Candle "
            f"{result['datetime']} "
            f"already exists | skipped",
            flush=True,
        )

    process_signal_quality(
        symbol=symbol,
        candles=candles,
        result=result,
        analysis_id=analysis_id,
        created_at=created_at,
    )

    if analysis_id is not None:
        (
            created,
            event_id,
            reason,
        ) = (
            create_signal_event_if_new(
                analysis_id=analysis_id,
                created_at=created_at,
                symbol=symbol,
                interval=INTERVAL,
                result=result,
            )
        )

        if created:
            print(
                "NEW SIGNAL EVENT | "

                f"{symbol} | "

                f"{result['signal']} | "

                f"Entry="
                f"{result['close']:.5f} | "

                f"SetupScore="
                f"{result['setup_score']}/100 | "

                f"Symbol signals="
                f"{count_signal_events(symbol)}",
                flush=True,
            )

        elif (
            reason
            == "CONTINUATION"
        ):
            print(
                f"{symbol} | "
                f"{result['signal']} "
                f"setup continues | "
                f"no new signal",
                flush=True,
            )

    new_trades = (
        ensure_virtual_trades(
            symbol
        )
    )

    if new_trades:
        print_new_virtual_trades(
            new_trades,
            candles,
        )

    trade_results = (
        evaluate_open_trades(
            candles,
            symbol,
        )
    )

    if trade_results:
        print_trade_results(
            trade_results
        )

        print_trade_summary(
            symbol
        )

        print_excursion_summary(
            symbol
        )

    outcomes = (
        evaluate_pending_signals(
            candles,
            symbol,
        )
    )

    if outcomes:
        for outcome in outcomes:
            print(
                "OUTCOME | "

                f"{outcome['symbol']} | "

                f"{outcome['signal']} | "

                f"Signal candle="
                f"{outcome['signal_time']} | "

                f"After="
                f"{outcome['horizon']}m | "

                f"{outcome['result']} | "

                f"Pips="
                f"{outcome['pips']:.2f} | "

                f"SetupScore="
                f"{outcome['score']}/100",
                flush=True,
            )

        print_outcome_summary(
            symbol
        )

    process_research_15m(
        symbol,
        candles,
    )

    process_multi_timeframe_quality(
        symbol=symbol,
        candles=all_candles,
        created_at=created_at,
    )


def analyze_once():
    for symbol in SYMBOLS:
        try:
            analyze_symbol(
                symbol
            )

        except Exception as error:
            print(
                f"MARKET ERROR | "
                f"{symbol} | "
                f"{type(error).__name__}: "
                f"{error}",
                flush=True,
            )

    try:
        send_daily_report_if_due()

    except Exception as error:
        print(
            "DAILY REPORT ERROR | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

    try:
        send_weekly_report_if_due()

    except Exception as error:
        print(
            "WEEKLY REPORT ERROR | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

    try:
        process_market_overview()

    except Exception as error:
        print(
            "MARKET OVERVIEW ERROR | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

    try:
        process_economic_calendar()

    except Exception as error:
        print(
            "CALENDAR ERROR | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

    try:
        process_news_digest()

    except Exception as error:
        print(
            "NEWS DIGEST ERROR | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

    try:
        process_health_monitor()

    except Exception as error:
        print(
            "HEALTH MONITOR ERROR | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )


def seconds_until_next_check():
    now = time.time()

    next_boundary = (
        (
            int(now)
            // INTERVAL_SECONDS
            + 1
        )
        * INTERVAL_SECONDS
    )

    target = (
        next_boundary
        + CANDLE_CLOSE_DELAY_SECONDS
    )

    return max(
        target - now,
        1,
    )


def main():
    init_db()

    initialize_signal_quality()

    if MULTI_TIMEFRAME_AVAILABLE:
        print(
            "MTF measurement bridge: ready | "
            "Signal=30min | Context=60min",
            flush=True,
        )

    else:
        print(
            "MTF measurement bridge: disabled | "
            f"Import error: "
            f"{MULTI_TIMEFRAME_IMPORT_ERROR}",
            flush=True,
        )

    init_daily_report_table()

    init_weekly_report_table()

    init_market_overview_table()

    init_economic_calendar()

    init_news_digest_tables()

    init_health_monitor_table()

    init_15m_research_tables()

    init_15m_trade_tables()

    init_user_subscription_tables()

    print(
        "Multi-market analysis engine started",
        flush=True,
    )

    print(
        "Symbols: "
        + ", ".join(
            SYMBOLS
        ),
        flush=True,
    )

    print(
        f"Stored candles: "
        f"{count_records()}",
        flush=True,
    )

    for symbol in SYMBOLS:
        print(
            f"{symbol} | "
            f"Records="
            f"{count_records(symbol)} | "
            f"Signals="
            f"{count_signal_events(symbol)} | "
            f"V2 Trades="
            f"{count_virtual_trades(symbol)} | "
            f"Open="
            f"{count_open_virtual_trades(symbol)}",
            flush=True,
        )

    print(
        f"Schedule: every "
        f"{INTERVAL}, "
        f"{CANDLE_CLOSE_DELAY_SECONDS}s "
        f"after candle boundary",
        flush=True,
    )

    print(
        "Daily report: "
        "00:05 UTC for previous day -> "
        "Free channel",
        flush=True,
    )

    print(
        "Weekly report: "
        "Monday 00:10 UTC for previous week -> "
        "Free channel",
        flush=True,
    )

    print(
        "Morning overview: "
        "weekdays 07:05-09:00 UTC -> "
        "Free channel",
        flush=True,
    )

    print(
        "Economic calendar: "
        "High Impact -> "
        "Free channel",
        flush=True,
    )

    print(
        "News digest: "
        "10:30 & 16:30 UTC -> "
        "Free channel",
        flush=True,
    )

    print(
        "Health monitor: "
        "stale market data >15min -> "
        "Private channel",
        flush=True,
    )

    print(
        "VIP channel: "
        "signal mirror enabled",
        flush=True,
    )

    print(
        "15m research: enabled, "
        "built from existing 5min data",
        flush=True,
    )

    print(
        "15m trade research: "
        "virtual execution enabled",
        flush=True,
    )

    print(
        "User system: database ready",
        flush=True,
    )

    print(
        "MAE/MFE tracking: enabled",
        flush=True,
    )

    try:
        start_user_bot_polling()

    except Exception as error:
        print(
            "USER BOT START ERROR | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

    try:
        send_vip_connection_test_once()

    except Exception as error:
        print(
            "VIP CHANNEL TEST ERROR | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

    try:
        analyze_once()

    except Exception as error:
        print(
            f"ERROR: "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

    while True:
        wait_seconds = (
            seconds_until_next_check()
        )

        next_check = (
            datetime.fromtimestamp(
                time.time()
                + wait_seconds,
                tz=timezone.utc,
            )
        )

        print(
            f"Next market check: "
            f"{next_check.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            flush=True,
        )

        time.sleep(
            wait_seconds
        )

        try:
            analyze_once()

        except Exception as error:
            print(
                f"ERROR: "
                f"{type(error).__name__}: "
                f"{error}",
                flush=True,
            )


if __name__ == "__main__":
    main()
