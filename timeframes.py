from datetime import (
    datetime,
    timedelta,
)


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

BASE_INTERVAL_MINUTES = 5

SUPPORTED_TIMEFRAMES = (
    30,
    60,
)


def _parse_datetime(
    value,
):
    if isinstance(
        value,
        datetime,
    ):
        return value

    return datetime.strptime(
        value,
        TIME_FORMAT,
    )


def _bucket_start(
    candle_time,
    target_minutes,
):
    minute = (
        candle_time.minute
        // target_minutes
        * target_minutes
    )

    return candle_time.replace(
        minute=minute,
        second=0,
        microsecond=0,
    )


def _expected_times(
    bucket_start,
    target_minutes,
):
    candle_count = (
        target_minutes
        // BASE_INTERVAL_MINUTES
    )

    return [
        bucket_start
        + timedelta(
            minutes=(
                index
                * BASE_INTERVAL_MINUTES
            )
        )
        for index in range(
            candle_count
        )
    ]


def aggregate_candles(
    candles,
    target_minutes,
):
    if (
        target_minutes
        not in SUPPORTED_TIMEFRAMES
    ):
        raise ValueError(
            "Unsupported target timeframe: "
            f"{target_minutes}min"
        )

    if not candles:
        return []

    groups = {}

    for candle in candles:
        candle_time = (
            _parse_datetime(
                candle["datetime"]
            )
        )

        bucket = (
            _bucket_start(
                candle_time,
                target_minutes,
            )
        )

        groups.setdefault(
            bucket,
            [],
        ).append(
            (
                candle_time,
                candle,
            )
        )

    aggregated = []

    for bucket in sorted(
        groups
    ):
        rows = sorted(
            groups[bucket],
            key=lambda item: item[0],
        )

        expected_times = (
            _expected_times(
                bucket,
                target_minutes,
            )
        )

        actual_times = [
            item[0]
            for item in rows
        ]

        if (
            actual_times
            != expected_times
        ):
            continue

        source = [
            item[1]
            for item in rows
        ]

        aggregated.append(
            {
                "datetime":
                    bucket.strftime(
                        TIME_FORMAT
                    ),

                "open":
                    float(
                        source[0][
                            "open"
                        ]
                    ),

                "high":
                    max(
                        float(
                            candle[
                                "high"
                            ]
                        )
                        for candle
                        in source
                    ),

                "low":
                    min(
                        float(
                            candle[
                                "low"
                            ]
                        )
                        for candle
                        in source
                    ),

                "close":
                    float(
                        source[-1][
                            "close"
                        ]
                    ),
            }
        )

    return aggregated


def build_signal_timeframes(
    five_minute_candles,
):
    candles_30m = (
        aggregate_candles(
            five_minute_candles,
            30,
        )
    )

    candles_60m = (
        aggregate_candles(
            five_minute_candles,
            60,
        )
    )

    return {
        "30min":
            candles_30m,

        "60min":
            candles_60m,
    }
