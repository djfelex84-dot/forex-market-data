"""Pure fixed-rule V4 event scanner for M30 research candles.

The two setup families are intentionally independent and use no H1, EMA, RSI,
session, or market-selection filter.  This keeps the first comparison focused
on the entry principles requested for V4 rather than on parameter selection.
"""

from collections import Counter
from datetime import datetime, timedelta


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
M30_MINUTES = 30
ATR_PERIOD = 14
TRAIN_END = datetime(2025, 1, 1)

RANGE_LOOKBACK = 12
BREAKOUT_MIN_ATR = 0.10
RETEST_TOLERANCE_ATR = 0.15
RETEST_FAIL_ATR = 0.20
RETEST_MAX_BARS = 4

FAKEOUT_MIN_SWEEP_ATR = 0.10
STRUCTURAL_STOP_BUFFER_ATR = 0.10
MIN_PREVIOUS_DAY_BARS = 24

SETUP_BREAKOUT_RETEST = "BREAKOUT_RETEST"
SETUP_FAKEOUT = "FAKEOUT"


def _parse_time(value):
    if isinstance(value, datetime):
        return value
    return datetime.strptime(str(value), TIME_FORMAT)


def _row_time(row):
    value = row.get("_time")
    return value if isinstance(value, datetime) else _parse_time(row["datetime"])


def _signal_close_time(row):
    return _row_time(row) + timedelta(minutes=M30_MINUTES)


def _setup_partition(row):
    """Keep pending setup state on only one side of the research split."""
    return "TRAIN" if _signal_close_time(row) < TRAIN_END else "VALIDATION"


def _is_contiguous(previous_row, current_row):
    return _row_time(current_row) - _row_time(previous_row) == timedelta(minutes=M30_MINUTES)


def _window_is_contiguous(rows, start, end):
    if start < 0 or end >= len(rows) or start > end:
        return False
    for index in range(start + 1, end + 1):
        if not _is_contiguous(rows[index - 1], rows[index]):
            return False
    return True


def _wilder_atr_series(rows, period=ATR_PERIOD):
    """Return ATR aligned to row indexes; values before initialization are None."""
    result = [None] * len(rows)
    if len(rows) < period + 1:
        return result

    true_ranges = [None] * len(rows)
    for index in range(1, len(rows)):
        high = float(rows[index]["high"])
        low = float(rows[index]["low"])
        previous_close = float(rows[index - 1]["close"])
        true_ranges[index] = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )

    initial = [true_ranges[index] for index in range(1, period + 1)]
    atr_value = sum(initial) / period
    result[period] = atr_value

    for index in range(period + 1, len(rows)):
        atr_value = ((atr_value * (period - 1)) + true_ranges[index]) / period
        result[index] = atr_value

    return result


def _previous_day_levels(rows):
    """Use the previous available trading date, so Monday naturally references Friday."""
    daily = {}
    for row in rows:
        timestamp = _row_time(row)
        day = timestamp.date()
        state = daily.setdefault(
            day,
            {"high": float(row["high"]), "low": float(row["low"]), "bars": 0},
        )
        state["high"] = max(state["high"], float(row["high"]))
        state["low"] = min(state["low"], float(row["low"]))
        state["bars"] += 1

    previous_for_day = {}
    previous_valid = None
    for day in sorted(daily):
        if previous_valid is not None:
            previous_for_day[day] = {
                "source_day": previous_valid,
                "high": daily[previous_valid]["high"],
                "low": daily[previous_valid]["low"],
            }
        if daily[day]["bars"] >= MIN_PREVIOUS_DAY_BARS:
            previous_valid = day

    return previous_for_day


def _event(*, setup, direction, row, entry_price, stop_price, level, atr_value, source):
    return {
        "setup": setup,
        "direction": direction,
        "signal_time": _signal_close_time(row),
        "entry_price": float(entry_price),
        "stop_price": float(stop_price),
        "level": float(level),
        "atr": float(atr_value),
        "source": source,
    }


def _scan_breakout_retest(rows, atr_values):
    events = []
    diagnostics = Counter()
    pending = None
    previous_partition = None

    for index, row in enumerate(rows):
        partition = _setup_partition(row)
        if previous_partition is not None and partition != previous_partition:
            if pending is not None:
                diagnostics["RESET_SPLIT_BOUNDARY"] += 1
            pending = None
        previous_partition = partition

        atr_value = atr_values[index]
        if atr_value is None or atr_value <= 0:
            continue

        if index > 0 and not _is_contiguous(rows[index - 1], row):
            if pending is not None:
                diagnostics["RESET_GAP"] += 1
            pending = None

        if pending is not None:
            direction = pending["direction"]
            level = pending["level"]
            fail_buffer = RETEST_FAIL_ATR * pending["atr"]

            if pending["stage"] == "WAIT_RETEST":
                if index > pending["retest_deadline"]:
                    diagnostics["EXPIRED_NO_RETEST"] += 1
                    pending = None
                else:
                    close = float(row["close"])
                    low = float(row["low"])
                    high = float(row["high"])

                    if direction == "BUY":
                        if close < level - fail_buffer:
                            diagnostics["FAILED_LEVEL"] += 1
                            pending = None
                        elif low <= level + (RETEST_TOLERANCE_ATR * pending["atr"]) and close >= level:
                            pending.update(
                                {
                                    "stage": "WAIT_CONFIRM",
                                    "retest_index": index,
                                    "retest_low": low,
                                    "retest_high": high,
                                }
                            )
                            diagnostics["RETEST_FOUND"] += 1
                            continue
                    else:
                        if close > level + fail_buffer:
                            diagnostics["FAILED_LEVEL"] += 1
                            pending = None
                        elif high >= level - (RETEST_TOLERANCE_ATR * pending["atr"]) and close <= level:
                            pending.update(
                                {
                                    "stage": "WAIT_CONFIRM",
                                    "retest_index": index,
                                    "retest_low": low,
                                    "retest_high": high,
                                }
                            )
                            diagnostics["RETEST_FOUND"] += 1
                            continue

            elif pending["stage"] == "WAIT_CONFIRM":
                if index != pending["retest_index"] + 1:
                    diagnostics["MISSED_CONFIRM_WINDOW"] += 1
                    pending = None
                else:
                    close = float(row["close"])
                    open_price = float(row["open"])
                    direction = pending["direction"]

                    if direction == "BUY":
                        confirmed = (
                            close > open_price
                            and close > pending["retest_high"]
                            and close > pending["level"]
                        )
                        stop_price = pending["retest_low"] - (
                            STRUCTURAL_STOP_BUFFER_ATR * pending["atr"]
                        )
                    else:
                        confirmed = (
                            close < open_price
                            and close < pending["retest_low"]
                            and close < pending["level"]
                        )
                        stop_price = pending["retest_high"] + (
                            STRUCTURAL_STOP_BUFFER_ATR * pending["atr"]
                        )

                    if confirmed:
                        events.append(
                            _event(
                                setup=SETUP_BREAKOUT_RETEST,
                                direction=direction,
                                row=row,
                                entry_price=close,
                                stop_price=stop_price,
                                level=pending["level"],
                                atr_value=pending["atr"],
                                source="LOCAL_RANGE_12_M30",
                            )
                        )
                        diagnostics["SIGNAL"] += 1
                    else:
                        diagnostics["CONFIRM_FAILED"] += 1
                    pending = None
                    continue

        if pending is not None:
            continue

        range_start = index - RANGE_LOOKBACK
        if range_start < 0:
            continue
        if not _window_is_contiguous(rows, range_start, index):
            diagnostics["NO_CONTIGUOUS_RANGE"] += 1
            continue

        history = rows[range_start:index]
        range_high = max(float(item["high"]) for item in history)
        range_low = min(float(item["low"]) for item in history)
        close = float(row["close"])
        open_price = float(row["open"])
        breakout_buffer = BREAKOUT_MIN_ATR * atr_value

        buy_breakout = close > range_high + breakout_buffer and close > open_price
        sell_breakout = close < range_low - breakout_buffer and close < open_price

        if buy_breakout:
            pending = {
                "stage": "WAIT_RETEST",
                "direction": "BUY",
                "level": range_high,
                "atr": atr_value,
                "breakout_index": index,
                "retest_deadline": index + RETEST_MAX_BARS,
            }
            diagnostics["BREAKOUT_BUY"] += 1
        elif sell_breakout:
            pending = {
                "stage": "WAIT_RETEST",
                "direction": "SELL",
                "level": range_low,
                "atr": atr_value,
                "breakout_index": index,
                "retest_deadline": index + RETEST_MAX_BARS,
            }
            diagnostics["BREAKOUT_SELL"] += 1
    return events, diagnostics


def _scan_fakeouts(rows, atr_values):
    events = []
    diagnostics = Counter()
    previous_levels = _previous_day_levels(rows)
    pending = None
    previous_partition = None
    used_level_keys = set()

    for index, row in enumerate(rows):
        partition = _setup_partition(row)
        if previous_partition is not None and partition != previous_partition:
            if pending is not None:
                diagnostics["RESET_SPLIT_BOUNDARY"] += 1
            pending = None
        previous_partition = partition

        atr_value = atr_values[index]
        if atr_value is None or atr_value <= 0:
            continue

        if index > 0 and not _is_contiguous(rows[index - 1], row):
            if pending is not None:
                diagnostics["RESET_GAP"] += 1
            pending = None

        if pending is not None:
            if index != pending["sweep_index"] + 1:
                diagnostics["MISSED_CONFIRM_WINDOW"] += 1
                pending = None
            else:
                close = float(row["close"])
                open_price = float(row["open"])
                direction = pending["direction"]

                if direction == "BUY":
                    confirmed = (
                        close > open_price
                        and close > pending["sweep_close"]
                        and close > pending["level"]
                    )
                    stop_price = pending["sweep_extreme"] - (
                        STRUCTURAL_STOP_BUFFER_ATR * pending["atr"]
                    )
                else:
                    confirmed = (
                        close < open_price
                        and close < pending["sweep_close"]
                        and close < pending["level"]
                    )
                    stop_price = pending["sweep_extreme"] + (
                        STRUCTURAL_STOP_BUFFER_ATR * pending["atr"]
                    )

                if confirmed:
                    events.append(
                        _event(
                            setup=SETUP_FAKEOUT,
                            direction=direction,
                            row=row,
                            entry_price=close,
                            stop_price=stop_price,
                            level=pending["level"],
                            atr_value=pending["atr"],
                            source=pending["source"],
                        )
                    )
                    used_level_keys.add(pending["level_key"])
                    diagnostics["SIGNAL"] += 1
                else:
                    diagnostics["CONFIRM_FAILED"] += 1

                pending = None
                continue

        if pending is not None:
            continue

        day = _row_time(row).date()
        levels = previous_levels.get(day)
        if levels is None:
            diagnostics["NO_PREVIOUS_DAY"] += 1
            continue

        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        sweep_buffer = FAKEOUT_MIN_SWEEP_ATR * atr_value

        pdh_key = (day, "PDH")
        pdl_key = (day, "PDL")

        swept_high = (
            pdh_key not in used_level_keys
            and high > levels["high"] + sweep_buffer
            and close < levels["high"]
        )
        swept_low = (
            pdl_key not in used_level_keys
            and low < levels["low"] - sweep_buffer
            and close > levels["low"]
        )

        if swept_high and swept_low:
            diagnostics["AMBIGUOUS_DOUBLE_SWEEP"] += 1
            continue

        if swept_high:
            pending = {
                "direction": "SELL",
                "level": levels["high"],
                "atr": atr_value,
                "sweep_index": index,
                "sweep_close": close,
                "sweep_extreme": high,
                "source": f"PDH::{levels['source_day'].isoformat()}",
                "level_key": pdh_key,
            }
            diagnostics["SWEEP_PDH"] += 1
        elif swept_low:
            pending = {
                "direction": "BUY",
                "level": levels["low"],
                "atr": atr_value,
                "sweep_index": index,
                "sweep_close": close,
                "sweep_extreme": low,
                "source": f"PDL::{levels['source_day'].isoformat()}",
                "level_key": pdl_key,
            }
            diagnostics["SWEEP_PDL"] += 1

    return events, diagnostics


def generate_v4_events(rows):
    """Pure research scanner. No storage, network, Telegram, broker, or live side effects."""
    if not rows:
        return {
            SETUP_BREAKOUT_RETEST: [],
            SETUP_FAKEOUT: [],
            "diagnostics": {},
        }

    atr_values = _wilder_atr_series(rows)
    breakout_events, breakout_diag = _scan_breakout_retest(
        rows,
        atr_values,
    )
    fakeout_events, fakeout_diag = _scan_fakeouts(
        rows,
        atr_values,
    )

    return {
        SETUP_BREAKOUT_RETEST: breakout_events,
        SETUP_FAKEOUT: fakeout_events,
        "diagnostics": {
            SETUP_BREAKOUT_RETEST: breakout_diag,
            SETUP_FAKEOUT: fakeout_diag,
        },
    }
