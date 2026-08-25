from datetime import (
    datetime,
    timedelta,
)

from config import (
    STOP_LOSS_ATR_MULTIPLIER,
    MIN_STOP_PIPS,
    TAKE_PROFIT_R_MULTIPLE,
    ASSUMED_SPREAD_PIPS,
    MAX_TRADE_MINUTES,
    TRADE_MODEL_VERSION,
    PIP_SIZE,
)

from storage import (
    get_signal_events_without_trades,
    save_virtual_trade,
    get_open_virtual_trades,
    close_virtual_trade,
    interval_minutes,
)


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def ensure_virtual_trades():
    created_trades = []

    events = get_signal_events_without_trades()

    for event in events:
        entry_price = float(
            event["entry_price"]
        )

        atr = float(
            event["atr"]
        )

        signal = event["signal"]

        atr_stop_distance = (
            atr
            * STOP_LOSS_ATR_MULTIPLIER
        )

        minimum_stop_distance = (
            MIN_STOP_PIPS
            * PIP_SIZE
        )

        stop_distance = max(
            atr_stop_distance,
            minimum_stop_distance,
        )

        target_distance = (
            stop_distance
            * TAKE_PROFIT_R_MULTIPLE
        )

        if signal == "BUY":
            stop_loss = (
                entry_price
                - stop_distance
            )

            take_profit = (
                entry_price
                + target_distance
            )

        elif signal == "SELL":
            stop_loss = (
                entry_price
                + stop_distance
            )

            take_profit = (
                entry_price
                - target_distance
            )

        else:
            continue

        risk_pips = (
            stop_distance
            / PIP_SIZE
        )

        reward_pips = (
            target_distance
            / PIP_SIZE
        )

        created, trade_id = save_virtual_trade(
            signal_event_id=
                event["signal_event_id"],

            created_at=
                event["created_at"],

            entry_candle_time=
                event["candle_time"],

            symbol=
                event["symbol"],

            interval=
                event["interval"],

            signal=
                signal,

            entry_price=
                entry_price,

            atr=
                atr,

            stop_loss=
                stop_loss,

            take_profit=
                take_profit,

            risk_pips=
                risk_pips,

            reward_pips=
                reward_pips,

            model_version=
                TRADE_MODEL_VERSION,

            spread_pips=
                ASSUMED_SPREAD_PIPS,

            max_hold_minutes=
                MAX_TRADE_MINUTES,
        )

        if created:
            created_trades.append(
                {
                    "id":
                        trade_id,

                    "signal":
                        signal,

                    "entry":
                        entry_price,

                    "stop_loss":
                        stop_loss,

                    "take_profit":
                        take_profit,

                    "risk_pips":
                        risk_pips,

                    "reward_pips":
                        reward_pips,

                    "spread_pips":
                        ASSUMED_SPREAD_PIPS,

                    "max_hold_minutes":
                        MAX_TRADE_MINUTES,
                }
            )

    return created_trades


def calculate_directional_pips(
    signal,
    entry_price,
    exit_price,
):
    if signal == "BUY":
        difference = (
            exit_price
            - entry_price
        )

    else:
        difference = (
            entry_price
            - exit_price
        )

    return (
        difference
        / PIP_SIZE
    )


def evaluate_open_trades(candles):
    results = []

    trades = get_open_virtual_trades()

    for trade in trades:
        signal_candle_open = (
            datetime.strptime(
                trade[
                    "entry_candle_time"
                ],
                TIME_FORMAT,
            )
        )

        candle_interval_minutes = (
            interval_minutes(
                trade["interval"]
            )
        )

        actual_entry_time = (
            signal_candle_open
            + timedelta(
                minutes=
                    candle_interval_minutes
            )
        )

        maximum_exit_time = (
            actual_entry_time
            + timedelta(
                minutes=
                    trade[
                        "max_hold_minutes"
                    ]
            )
        )

        entry_price = float(
            trade["entry_price"]
        )

        stop_loss = float(
            trade["stop_loss"]
        )

        take_profit = float(
            trade["take_profit"]
        )

        risk_pips = float(
            trade["risk_pips"]
        )

        reward_pips = float(
            trade["reward_pips"]
        )

        spread_pips = float(
            trade["spread_pips"]
        )

        signal = trade["signal"]

        for candle in candles:
            candle_open = (
                datetime.strptime(
                    candle["datetime"],
                    TIME_FORMAT,
                )
            )

            candle_close_time = (
                candle_open
                + timedelta(
                    minutes=
                        candle_interval_minutes
                )
            )

            if candle_open < actual_entry_time:
                continue

            high = float(
                candle["high"]
            )

            low = float(
                candle["low"]
            )

            close = float(
                candle["close"]
            )

            if signal == "BUY":
                stop_hit = (
                    low <= stop_loss
                )

                target_hit = (
                    high >= take_profit
                )

            else:
                stop_hit = (
                    high >= stop_loss
                )

                target_hit = (
                    low <= take_profit
                )

            if (
                stop_hit
                and target_hit
            ):
                closed = close_virtual_trade(
                    trade_id=
                        trade["id"],

                    status=
                        "AMBIGUOUS",

                    exit_candle_time=
                        candle["datetime"],

                    exit_price=
                        None,

                    exit_reason=
                        "SL_AND_TP_SAME_CANDLE",

                    gross_pnl_pips=
                        None,

                    net_pnl_pips=
                        None,

                    r_multiple=
                        None,
                )

                if closed:
                    results.append(
                        {
                            "trade_id":
                                trade["id"],

                            "signal":
                                signal,

                            "result":
                                "AMBIGUOUS",

                            "candle_time":
                                candle[
                                    "datetime"
                                ],

                            "gross_pips":
                                None,

                            "net_pips":
                                None,

                            "r":
                                None,
                        }
                    )

                break

            if target_hit:
                gross_pips = (
                    reward_pips
                )

                net_pips = (
                    gross_pips
                    - spread_pips
                )

                r_multiple = (
                    net_pips
                    / risk_pips
                )

                closed = close_virtual_trade(
                    trade_id=
                        trade["id"],

                    status=
                        "CLOSED",

                    exit_candle_time=
                        candle["datetime"],

                    exit_price=
                        take_profit,

                    exit_reason=
                        "TAKE_PROFIT",

                    gross_pnl_pips=
                        gross_pips,

                    net_pnl_pips=
                        net_pips,

                    r_multiple=
                        r_multiple,
                )

                if closed:
                    results.append(
                        {
                            "trade_id":
                                trade["id"],

                            "signal":
                                signal,

                            "result":
                                "TAKE_PROFIT",

                            "candle_time":
                                candle[
                                    "datetime"
                                ],

                            "gross_pips":
                                gross_pips,

                            "net_pips":
                                net_pips,

                            "r":
                                r_multiple,
                        }
                    )

                break

            if stop_hit:
                gross_pips = (
                    -risk_pips
                )

                net_pips = (
                    gross_pips
                    - spread_pips
                )

                r_multiple = (
                    net_pips
                    / risk_pips
                )

                closed = close_virtual_trade(
                    trade_id=
                        trade["id"],

                    status=
                        "CLOSED",

                    exit_candle_time=
                        candle["datetime"],

                    exit_price=
                        stop_loss,

                    exit_reason=
                        "STOP_LOSS",

                    gross_pnl_pips=
                        gross_pips,

                    net_pnl_pips=
                        net_pips,

                    r_multiple=
                        r_multiple,
                )

                if closed:
                    results.append(
                        {
                            "trade_id":
                                trade["id"],

                            "signal":
                                signal,

                            "result":
                                "STOP_LOSS",

                            "candle_time":
                                candle[
                                    "datetime"
                                ],

                            "gross_pips":
                                gross_pips,

                            "net_pips":
                                net_pips,

                            "r":
                                r_multiple,
                        }
                    )

                break

            if (
                candle_close_time
                >= maximum_exit_time
            ):
                gross_pips = (
                    calculate_directional_pips(
                        signal=
                            signal,

                        entry_price=
                            entry_price,

                        exit_price=
                            close,
                    )
                )

                net_pips = (
                    gross_pips
                    - spread_pips
                )

                r_multiple = (
                    net_pips
                    / risk_pips
                )

                closed = close_virtual_trade(
                    trade_id=
                        trade["id"],

                    status=
                        "CLOSED",

                    exit_candle_time=
                        candle["datetime"],

                    exit_price=
                        close,

                    exit_reason=
                        "TIMEOUT",

                    gross_pnl_pips=
                        gross_pips,

                    net_pnl_pips=
                        net_pips,

                    r_multiple=
                        r_multiple,
                )

                if closed:
                    results.append(
                        {
                            "trade_id":
                                trade["id"],

                            "signal":
                                signal,

                            "result":
                                "
