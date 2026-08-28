from datetime import datetime, timedelta


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
BASE_INTERVAL_MINUTES = 5


def _parse_time(value):
    return datetime.strptime(
        value,
        TIME_FORMAT,
    )


def _bucket_start(
    value,
    minutes,
):
    minute = (
        value.minute
        // minutes
        * minutes
    )

    return value.replace(
        minute=minute,
        second=0,
        microsecond=0,
    )


def aggregate_candles(
    candles,
    target_minutes,
):
    if target_minutes <= 0:
        raise ValueError(
            "target_minutes must be positive"
        )

    if (
        target_minutes
        % BASE_INTERVAL_MINUTES
        != 0
    ):
        raise ValueError(
            "target_minutes must be divisible by 5"
        )

    if not candles:
        return []

    expected_count = (
        target_minutes
        // BASE_INTERVAL_MINUTES
    )

    groups = {}

    for candle in candles:
        candle_time = _parse_time(
            candle["datetime"]
        )

        bucket = _bucket_start(
            candle_time,
            target_minutes,
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

    result = []

    for bucket in sorted(groups):
        group = sorted(
            groups[bucket],
            key=lambda item: item[0],
        )

        if len(group) != expected_count:
            continue

        expected_times = [
            bucket
            + timedelta(
                minutes=(
                    BASE_INTERVAL_MINUTES
                    * i
                )
            )
            for i in range(
                expected_count
            )
        ]

        actual_times = [
            item[0]
            for item in group
        ]

        if actual_times != expected_times:
            continue

        rows = [
            item[1]
            for item in group
        ]

        result.append(
            {
                "datetime":
                    bucket.strftime(
                        TIME_FORMAT
                    ),

                "open":
                    float(
                        rows[0]["open"]
                    ),

                "high":
                    max(
                        float(row["high"])
                        for row in rows
                    ),

                "low":
                    min(
                        float(row["low"])
                        for row in rows
                    ),

                "close":
                    float(
                        rows[-1]["close"]
                    ),
            }
        )

    return result


def build_signal_timeframes(
    candles,
):
    return {
        "30min":
            aggregate_candles(
                candles,
                30,
            ),

        "60min":
            aggregate_candles(
                candles,
                60,
            ),
    }
