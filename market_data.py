import time
import requests

from datetime import (
    datetime,
    timedelta,
    timezone,
)

from config import (
    TWELVE_DATA_API_KEY,
    SYMBOL,
    INTERVAL,
    CANDLE_LIMIT,
)


API_URL = (
    "https://api.twelvedata.com/"
    "time_series"
)

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


# First request + 2 retries.
MAX_FETCH_ATTEMPTS = 3

# Wait between retries when
# Twelve Data is exactly one
# closed candle behind.
RETRY_DELAY_SECONDS = 10


def interval_to_timedelta(
    interval
):
    if interval.endswith("min"):
        minutes = int(
            interval.replace(
                "min",
                "",
            )
        )

        return timedelta(
            minutes=minutes
        )

    if interval.endswith("h"):
        hours = int(
            interval.replace(
                "h",
                "",
            )
        )

        return timedelta(
            hours=hours
        )

    raise ValueError(
        f"Unsupported interval: "
        f"{interval}"
    )


def interval_to_seconds(
    interval
):
    return int(
        interval_to_timedelta(
            interval
        ).total_seconds()
    )


INTERVAL_DELTA = (
    interval_to_timedelta(
        INTERVAL
    )
)

INTERVAL_SECONDS = (
    interval_to_seconds(
        INTERVAL
    )
)


def get_expected_latest_candle_open(
    now
):
    timestamp = int(
        now.timestamp()
    )

    current_boundary = (
        timestamp
        // INTERVAL_SECONDS
        * INTERVAL_SECONDS
    )

    expected_open_timestamp = (
        current_boundary
        - INTERVAL_SECONDS
    )

    return datetime.fromtimestamp(
        expected_open_timestamp,
        tz=timezone.utc,
    )


def request_candles(
    symbol
):
    params = {
        "symbol": symbol,
        "interval": INTERVAL,
        "outputsize": CANDLE_LIMIT,
        "timezone": "UTC",
        "apikey": TWELVE_DATA_API_KEY,
    }

    response = requests.get(
        API_URL,
        params=params,
        timeout=15,
    )

    response.raise_for_status()

    data = response.json()

    if data.get(
        "status"
    ) == "error":
        raise RuntimeError(
            data.get(
                "message",
                (
                    "Twelve Data "
                    "API error"
                ),
            )
        )

    values = data.get(
        "values"
    )

    if not values:
        raise RuntimeError(
            "No candle data "
            f"received for {symbol}"
        )

    return values


def prepare_closed_candles(
    values,
    now,
):
    candles = []

    for candle in values:
        candle_open = (
            datetime.strptime(
                candle["datetime"],
                TIME_FORMAT,
            ).replace(
                tzinfo=timezone.utc
            )
        )

        candle_close = (
            candle_open
            + INTERVAL_DELTA
        )

        # Never use a candle that
        # has not fully closed yet.
        if candle_close > now:
            continue

        candles.append(
            {
                "datetime":
                    candle["datetime"],

                "open":
                    float(
                        candle["open"]
                    ),

                "high":
                    float(
                        candle["high"]
                    ),

                "low":
                    float(
                        candle["low"]
                    ),

                "close":
                    float(
                        candle["close"]
                    ),
            }
        )

    if not candles:
        return []

    # Twelve Data returns newest
    # candles first.
    #
    # Indicators require:
    # old -> new.
    candles.reverse()

    return candles


def fetch_candles(
    symbol=None
):
    if not TWELVE_DATA_API_KEY:
        raise RuntimeError(
            "TWELVE_DATA_API_KEY "
            "is not set"
        )

    if symbol is None:
        symbol = SYMBOL

    for attempt in range(
        1,
        MAX_FETCH_ATTEMPTS + 1,
    ):
        now = datetime.now(
            timezone.utc
        )

        expected_latest = (
            get_expected_latest_candle_open(
                now
            )
        )

        values = request_candles(
            symbol
        )

        candles = (
            prepare_closed_candles(
                values,
                now,
            )
        )

        if not candles:
            raise RuntimeError(
                "No closed candles "
                f"available for {symbol}"
            )

        latest_candle = (
            datetime.strptime(
                candles[-1][
                    "datetime"
                ],
                TIME_FORMAT,
            ).replace(
                tzinfo=timezone.utc
            )
        )

        # Perfect:
        # Twelve Data already has
        # the candle we expect.
        if (
            latest_candle
            == expected_latest
        ):
            if attempt > 1:
                print(
                    "DATA READY | "
                    f"{symbol} | "
                    f"Candle="
                    f"{latest_candle.strftime(TIME_FORMAT)} | "
                    f"Attempt={attempt}",
                    flush=True,
                )

            return candles

        lag = (
            expected_latest
            - latest_candle
        )

        # API is exactly one candle
        # behind. This is the case
        # where a short retry is useful.
        if (
            lag == INTERVAL_DELTA
            and attempt
            < MAX_FETCH_ATTEMPTS
        ):
            print(
                "DATA WAIT | "
                f"{symbol} | "
                "Expected="
                f"{expected_latest.strftime(TIME_FORMAT)} | "
                "Received="
                f"{latest_candle.strftime(TIME_FORMAT)} | "
                f"Retry in "
                f"{RETRY_DELAY_SECONDS}s | "
                f"Attempt="
                f"{attempt}/"
                f"{MAX_FETCH_ATTEMPTS}",
                flush=True,
            )

            time.sleep(
                RETRY_DELAY_SECONDS
            )

            continue

        # If the market is closed or
        # there is a larger historical
        # gap, repeated requests are
        # unlikely to help.
        if lag > INTERVAL_DELTA:
            print(
                "DATA GAP | "
                f"{symbol} | "
                "Expected="
                f"{expected_latest.strftime(TIME_FORMAT)} | "
                "Latest="
                f"{latest_candle.strftime(TIME_FORMAT)} | "
                "No retry",
                flush=True,
            )

            return candles

        # If all short retries were
        # exhausted, return the latest
        # valid closed candles.
        if (
            attempt
            >= MAX_FETCH_ATTEMPTS
        ):
            print(
                "DATA DELAY | "
                f"{symbol} | "
                "Expected="
                f"{expected_latest.strftime(TIME_FORMAT)} | "
                "Latest="
                f"{latest_candle.strftime(TIME_FORMAT)} | "
                "Retries exhausted",
                flush=True,
            )

            return candles

        # Defensive fallback for any
        # unusual timestamp situation.
        return candles

    raise RuntimeError(
        "Unable to fetch candles "
        f"for {symbol}"
    )
