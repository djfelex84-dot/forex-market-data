from datetime import (
    datetime,
    timedelta,
)

from config import (
    STOP_LOSS_ATR_MULTIPLIER,
    TAKE_PROFIT_R_MULTIPLE,
    MAX_TRADE_MINUTES,
    TRADE_MODEL_VERSION,
    get_instrument_config,
)

from storage import (
    get_signal_events_without_trades,
    save_virtual_trade,
    get_open_virtual_trades,
    close_virtual_trade,
    interval_minutes,
)


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def ensure_virtual_trades(
    symbol,
):
    created_trades = []

    instrument = (
        get_instrument_config(
            symbol
        )
    )

    pip_size = instrument[
        "pip_size"
    ]

    min_stop_pips = instrument[
        "min_stop_pips"
    ]

    assumed_spread_pips = instrument[
        "assumed_spread_pips"
    ]

    events = (
        get_signal_events_without_trades(
            symbol=symbol
        )
    )

    for event in events:
        entry_price = float(
            event["entry_price"]
        )

        atr = float(
            event["atr"]
        )

        signal = event[
            "signal"
        ]

        atr_stop_distance = (
            atr
            * STOP_LOSS_ATR_MULTIPLIER
        )

        minimum_stop_distance = (
            min_stop_pips
            * pip_size
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
            / pip_size
        )

        reward_pips = (
            target_distance
            / pip_size
        )

        created, trade_id = (
            save_virtual_trade(
                signal_event_id=(
                    event[
                        "signal_event_id"
                    ]
                ),

                created_at=(
                    event[
                        "created_at"
                    ]
                ),

                entry_candle_time=(
                    event[
                        "candle_time"
                    ]
                ),

                symbol=(
                    event[
                        "symbol"
                    ]
                ),

                interval=(
                    event[
                        "interval"
                    ]
                ),

                signal=signal,

                entry_price=(
                    entry_price
                ),

                atr=atr,

                stop_loss=(
                    stop_loss
                ),

                take_profit=(
                    take_profit
                ),

                risk_pips=(
                    risk_pips
                ),

                reward_pips=(
                    reward_pips
                ),

                model_version=(
                    TRADE_MODEL_VERSION
                ),

                spread_pips=(
                    assumed_spread_pips
                ),

                max_hold_minutes=(
                    MAX_TRADE_MINUTES
                ),
            )
        )

        if created:
            created_trades.append(
                {
                    "id":
                        trade_id,

                    "symbol":
                        event[
                            "symbol"
                        ],

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
                        assumed_spread_pips,

                    "max_hold_minutes":
                        MAX_TRADE_MINUTES,

                    "entry_candle_time":
                        event[
                            "candle_time"
                        ],

                    "interval":
                        event[
                            "interval"
                        ],
                }
            )

    return created_trades


def calculate_directional_pips(
    signal,
    entry_price,
    exit_price,
    pip_size,
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
        / pip_size
    )


def evaluate_open_trades(
    candles,
    symbol,
):
    results = []

    instrument = (
        get_instrument_config(
            symbol
        )
    )

    pip_size = instrument[
        "pip_size"
    ]

    open_trades = (
        get_open_virtual_trades(
            symbol=symbol
        )
    )

    for trade in open_trades:
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
                trade[
                    "interval"
                ]
            )
        )

        actual_entry_time = (
            signal_candle_open
            + timedelta(
                minutes=(
                    candle_interval_minutes
                )
            )
        )

        maximum_exit_time = (
            actual_entry_time
            + timedelta(
                minutes=(
                    trade[
                        "max_hold_minutes"
                    ]
                )
            )
        )

        entry_price = float(
            trade[
                "entry_price"
            ]
        )

        stop_loss = float(
            trade[
                "stop_loss"
            ]
        )

        take_profit = float(
            trade[
                "take_profit"
            ]
        )

        risk_pips = float(
            trade[
                "risk_pips"
            ]
        )

        reward_pips = float(
            trade[
                "reward_pips"
            ]
        )

        spread_pips = float(
            trade[
                "spread_pips"
            ]
        )

        signal = trade[
            "signal"
        ]

        for candle in candles:
            candle_open = (
                datetime.strptime(
                    candle[
                        "datetime"
                    ],
                    TIME_FORMAT,
                )
            )

            if (
                candle_open
                < actual_entry_time
            ):
                continue

            candle_close_time = (
                candle_open
                + timedelta(
                    minutes=(
                        candle_interval_minutes
                    )
                )
            )

            high = float(
                candle[
                    "high"
                ]
            )

            low = float(
                candle[
                    "low"
                ]
            )

            close = float(
                candle[
                    "close"
                ]
            )

            if signal == "BUY":
                stop_hit = (
                    low
                    <= stop_loss
                )

                target_hit = (
                    high
                    >= take_profit
                )

            elif signal == "SELL":
                stop_hit = (
                    high
                    >= stop_loss
                )

                target_hit = (
                    low
                    <= take_profit
                )

            else:
                continue

            if (
                stop_hit
                and target_hit
            ):
                closed = (
                    close_virtual_trade(
                        trade_id=(
                            trade["id"]
                        ),

                        status=(
                            "AMBIGUOUS"
                        ),

                        exit_candle_time=(
                            candle[
                                "datetime"
                            ]
                        ),

                        exit_price=None,

                        exit_reason=(
                            "SL_AND_TP_"
                            "SAME_CANDLE"
                        ),

                        gross_pnl_pips=None,
                        net_pnl_pips=None,
                        r_multiple=None,
                    )
                )

                if closed:
                    results.append(
                        {
                            "trade_id":
                                trade[
                                    "id"
                                ],

                            "symbol":
                                symbol,

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

                closed = (
                    close_virtual_trade(
                        trade_id=(
                            trade["id"]
                        ),

                        status="CLOSED",

                        exit_candle_time=(
                            candle[
                                "datetime"
                            ]
                        ),

                        exit_price=(
                            take_profit
                        ),

                        exit_reason=(
                            "TAKE_PROFIT"
                        ),

                        gross_pnl_pips=(
                            gross_pips
                        ),

                        net_pnl_pips=(
                            net_pips
                        ),

                        r_multiple=(
                            r_multiple
                        ),
                    )
                )

                if closed:
                    results.append(
                        {
                            "trade_id":
                                trade[
                                    "id"
                                ],

                            "symbol":
                                symbol,

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

                closed = (
                    close_virtual_trade(
                        trade_id=(
                            trade["id"]
                        ),

                        status="CLOSED",

                        exit_candle_time=(
                            candle[
                                "datetime"
                            ]
                        ),

                        exit_price=(
                            stop_loss
                        ),

                        exit_reason=(
                            "STOP_LOSS"
                        ),

                        gross_pnl_pips=(
                            gross_pips
                        ),

                        net_pnl_pips=(
                            net_pips
                        ),

                        r_multiple=(
                            r_multiple
                        ),
                    )
                )

                if closed:
                    results.append(
                        {
                            "trade_id":
                                trade[
                                    "id"
                                ],

                            "symbol":
                                symbol,

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
                        signal=signal,

                        entry_price=(
                            entry_price
                        ),

                        exit_price=(
                            close
                        ),

                        pip_size=(
                            pip_size
                        ),
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

                closed = (
                    close_virtual_trade(
                        trade_id=(
                            trade["id"]
                        ),

                        status="CLOSED",

                        exit_candle_time=(
                            candle[
                                "datetime"
                            ]
                        ),

                        exit_price=(
                            close
                        ),

                        exit_reason=(
                            "TIMEOUT"
                        ),

                        gross_pnl_pips=(
                            gross_pips
                        ),

                        net_pnl_pips=(
                            net_pips
                        ),

                        r_multiple=(
                            r_multiple
                        ),
                    )
                )

                if closed:
                    results.append(
                        {
                            "trade_id":
                                trade[
                                    "id"
                                ],

                            "symbol":
                                symbol,

                            "signal":
                                signal,

                            "result":
                                "TIMEOUT",

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

    return results
