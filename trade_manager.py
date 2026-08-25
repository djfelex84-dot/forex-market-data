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
    update_trade_excursions,
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


def calculate_candle_excursions(
    signal,
    entry_price,
    high,
    low,
    pip_size,
):
    if signal == "BUY":
        adverse_pips = max(
            (
                entry_price
                - low
            )
            / pip_size,
            0.0,
        )

        favorable_pips = max(
            (
                high
                - entry_price
            )
            / pip_size,
            0.0,
        )

    elif signal == "SELL":
        adverse_pips = max(
            (
                high
                - entry_price
            )
            / pip_size,
            0.0,
        )

        favorable_pips = max(
            (
                entry_price
                - low
            )
            / pip_size,
            0.0,
        )

    else:
        adverse_pips = 0.0
        favorable_pips = 0.0

    return (
        adverse_pips,
        favorable_pips,
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

        current_mae = (
            float(
                trade[
                    "mae_pips"
                ]
            )
            if trade[
                "mae_pips"
            ] is not None
            else 0.0
        )

        current_mfe = (
            float(
                trade[
                    "mfe_pips"
                ]
            )
            if trade[
                "mfe_pips"
            ] is not None
            else 0.0
        )

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

            # =========================
            # AMBIGUOUS CANDLE
            # =========================
            #
            # Both SL and TP were
            # touched inside one candle.
            # OHLC cannot tell us
            # which happened first.
            #
            # We therefore do NOT use
            # this candle for MAE/MFE.

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

                            "mae_pips":
                                current_mae,

                            "mfe_pips":
                                current_mfe,
                        }
                    )

                break

            # =========================
            # TAKE PROFIT
            # =========================
            #
            # We know price reached TP.
            # We do NOT use the entire
            # candle high/low because
            # some movement may have
            # happened after exit.

            if target_hit:
                current_mfe = max(
                    current_mfe,
                    reward_pips,
                )

                update_trade_excursions(
                    trade_id=(
                        trade["id"]
                    ),

                    mae_pips=(
                        current_mae
                    ),

                    mfe_pips=(
                        current_mfe
                    ),
                )

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

                            "mae_pips":
                                current_mae,

                            "mfe_pips":
                                current_mfe,
                        }
                    )

                break

            # =========================
            # STOP LOSS
            # =========================

            if stop_hit:
                current_mae = max(
                    current_mae,
                    risk_pips,
                )

                update_trade_excursions(
                    trade_id=(
                        trade["id"]
                    ),

                    mae_pips=(
                        current_mae
                    ),

                    mfe_pips=(
                        current_mfe
                    ),
                )

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

                            "mae_pips":
                                current_mae,

                            "mfe_pips":
                                current_mfe,
                        }
                    )

                break

            # =========================
            # NORMAL OPEN CANDLE
            # =========================
            #
            # No exit occurred, so the
            # entire candle belongs to
            # the lifetime of the trade.

            (
                candle_mae,
                candle_mfe,
            ) = (
                calculate_candle_excursions(
                    signal=signal,

                    entry_price=(
                        entry_price
                    ),

                    high=high,
                    low=low,

                    pip_size=(
                        pip_size
                    ),
                )
            )

            current_mae = max(
                current_mae,
                candle_mae,
            )

            current_mfe = max(
                current_mfe,
                candle_mfe,
            )

            update_trade_excursions(
                trade_id=(
                    trade["id"]
                ),

                mae_pips=(
                    current_mae
                ),

                mfe_pips=(
                    current_mfe
                ),
            )

            # =========================
            # TIME EXIT
            # =========================
            #
            # Timeout occurs at candle
            # close, so using the full
            # candle for MAE/MFE is valid.

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

                            "mae_pips":
                                current_mae,

                            "mfe_pips":
                                current_mfe,
                        }
                    )

                break

    return results
