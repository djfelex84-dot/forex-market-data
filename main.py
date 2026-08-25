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
)

from daily_report import (
    init_daily_report_table,
    send_daily_report_if_due,
)

from economic_calendar import (
    init_economic_calendar,
    process_economic_calendar,
)


CANDLE_CLOSE_DELAY_SECONDS = 15


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
        total = row["total"]

        wins = (
            row["wins"]
            or 0
        )

        win_rate = (
            wins / total * 100
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


def analyze_symbol(
    symbol,
):
    candles = (
        fetch_candles(
            symbol
        )
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
        process_economic_calendar()

    except Exception as error:
        print(
            "CALENDAR ERROR | "
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

    init_daily_report_table()

    init_economic_calendar()

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
        "Economic calendar: "
        "High Impact -> "
        "Free channel",
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
