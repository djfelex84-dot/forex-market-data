from datetime import (
    datetime,
    timedelta,
    timezone,
)

from config import (
    OUTCOME_HORIZONS_MINUTES,
    PIP_SIZE,
)

from storage import (
    get_pending_signal_events,
    outcome_exists,
    save_signal_event_outcome,
)


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def evaluate_pending_signals(candles):

    candle_map = {
        candle["datetime"]: candle
        for candle in candles
    }

    evaluated = []

    signals = (
        get_pending_signal_events()
    )

    for signal in signals:

        signal_time = datetime.strptime(
            signal["candle_time"],
            TIME_FORMAT,
        )

        entry_price = float(
            signal["entry_price"]
        )

        direction = signal["signal"]

        for horizon in (
            OUTCOME_HORIZONS_MINUTES
        ):

            if outcome_exists(
                signal["signal_event_id"],
                horizon,
            ):
                continue

            target_time = (
                signal_time
                + timedelta(
                    minutes=horizon
                )
            )

            target_key = (
                target_time.strftime(
                    TIME_FORMAT
                )
            )

            target_candle = (
                candle_map.get(
                    target_key
                )
            )

            if target_candle is None:
                continue

            target_close = float(
                target_candle["close"]
            )

            if direction == "BUY":

                directional_change = (
                    target_close
                    - entry_price
                )

            elif direction == "SELL":

                directional_change = (
                    entry_price
                    - target_close
                )

            else:
                continue

            directional_pips = (
                directional_change
                / PIP_SIZE
            )

            if directional_pips > 0.05:
                outcome = "WIN"

            elif directional_pips < -0.05:
                outcome = "LOSS"

            else:
                outcome = "FLAT"

            evaluated_at = (
                datetime.now(
                    timezone.utc
                ).strftime(
                    TIME_FORMAT
                )
            )

            save_signal_event_outcome(
                signal_event_id=
                    signal[
                        "signal_event_id"
                    ],

                horizon_minutes=
                    horizon,

                target_candle_time=
                    target_key,

                target_close=
                    target_close,

                directional_pips=
                    directional_pips,

                result=
                    outcome,

                evaluated_at=
                    evaluated_at,
            )

            evaluated.append(
                {
                    "signal":
                        direction,

                    "signal_time":
                        signal[
                            "candle_time"
                        ],

                    "horizon":
                        horizon,

                    "pips":
                        directional_pips,

                    "result":
                        outcome,

                    "score":
                        signal[
                            "setup_score"
                        ],
                }
            )

    return evaluated
