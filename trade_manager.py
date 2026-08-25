from datetime import datetime


from config import (
    STOP_LOSS_ATR_MULTIPLIER,
    TAKE_PROFIT_ATR_MULTIPLIER,
    PIP_SIZE,
)


from storage import (
    get_signal_events_without_trades,
    save_virtual_trade,

    get_open_virtual_trades,
    close_virtual_trade,
)


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def ensure_virtual_trades():

    created_trades = []

    events = (
        get_signal_events_without_trades()
    )

    for event in events:

        entry_price = float(
            event["entry_price"]
        )

        atr = float(
            event["atr"]
        )

        signal = event["signal"]

        stop_distance = (
            atr
            * STOP_LOSS_ATR_MULTIPLIER
        )

        target_distance = (
            atr
            * TAKE_PROFIT_ATR_MULTIPLIER
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

        created, trade_id = (
            save_virtual_trade(

                signal_event_id=
                    event[
                        "signal_event_id"
                    ],

                created_at=
                    event[
                        "created_at"
                    ],

                entry_candle_time=
                    event[
                        "candle_time"
                    ],

                symbol=
                    event[
                        "symbol"
                    ],

                interval=
                    event[
                        "interval"
                    ],

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
            )
        )

        if created:

            created_trades.append(
                {
                    "id":
                        trade_id,

                    "signal":
                        signal,

                    "entry_time":
                        event[
                            "candle_time"
                        ],

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
                }
            )

    return created_trades


def evaluate_open_trades(candles):

    results = []

    trades = (
        get_open_virtual_trades()
    )

    for trade in trades:

        entry_time = datetime.strptime(
            trade[
                "entry_candle_time"
            ],
            TIME_FORMAT,
        )

        for candle in candles:

            candle_time = datetime.strptime(
                candle["datetime"],
                TIME_FORMAT,
            )

            # Entry happens at the CLOSE
            # of the signal candle.
            #
            # Therefore we only inspect
            # candles AFTER signal candle.
            if candle_time <= entry_time:
                continue

            high = float(
                candle["high"]
            )

            low = float(
                candle["low"]
            )

            signal = trade["signal"]

            stop_loss = float(
                trade["stop_loss"]
            )

            take_profit = float(
                trade["take_profit"]
            )

            if signal == "BUY":

                stop_hit = (
                    low <= stop_loss
                )

                target_hit = (
                    high >= take_profit
                )

            elif signal == "SELL":

                stop_hit = (
                    high >= stop_loss
                )

                target_hit = (
                    low <= take_profit
                )

            else:
                continue

            # Both levels touched
            # during same 5-minute candle.
            #
            # OHLC does not tell us which
            # happened first.
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

                    pnl_pips=
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

                            "pips":
                                None,
                        }
                    )

                break

            if target_hit:

                pnl_pips = float(
                    trade[
                        "reward_pips"
                    ]
                )

                closed = close_virtual_trade(
                    trade_id=
                        trade["id"],

                    status=
                        "CLOSED",

                    exit_candle_time=
                        candle[
                            "datetime"
                        ],

                    exit_price=
                        take_profit,

                    exit_reason=
                        "TAKE_PROFIT",

                    pnl_pips=
                        pnl_pips,
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

                            "pips":
                                pnl_pips,
                        }
                    )

                break

            if stop_hit:

                pnl_pips = -float(
                    trade[
                        "risk_pips"
                    ]
                )

                closed = close_virtual_trade(
                    trade_id=
                        trade["id"],

                    status=
                        "CLOSED",

                    exit_candle_time=
                        candle[
                            "datetime"
                        ],

                    exit_price=
                        stop_loss,

                    exit_reason=
                        "STOP_LOSS",

                    pnl_pips=
                        pnl_pips,
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

                            "pips":
                                pnl_pips,
                        }
                    )

                break

    return results
