"""Research-only Dukascopy tick normalization and deterministic aggregation.

This module deliberately has no production storage, Telegram, or live imports.
It can decode Dukascopy's public hourly BI5 tick artifacts and derive BID, ASK,
and tick-midpoint bars.  Strict M1-path aggregation and complete-hour direct
tick aggregation are separate policies.  Silent minutes are always flagged;
prices are never forward-filled.
"""

import hashlib
import json
import lzma
import math
import struct
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
DUKASCOPY_ARCHIVE_ROOT = "https://datafeed.dukascopy.com/datafeed"
TICK_RECORD = struct.Struct(">IIIff")
CANDLE_RECORD = struct.Struct(">IIIIIf")
SIDES = ("bid", "ask", "mid")
OFFER_SIDES = ("bid", "ask")
OHLC_FIELDS = ("open", "high", "low", "close")
PRICE_SCALES = {
    "EUR/USD": 100_000.0,
    "GBP/USD": 100_000.0,
}
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}
DOWNLOAD_USER_AGENT = "AS-V4-Research/1.0"


def parse_utc(value):
    """Return a timezone-naive UTC datetime used by the existing V4 scanner."""
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value).strip().replace("T", " ").removesuffix("Z")
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            result = datetime.strptime(text, TIME_FORMAT)

    if result.tzinfo is not None:
        result = result.astimezone(timezone.utc).replace(tzinfo=None)
    return result


def floor_minute(timestamp):
    timestamp = parse_utc(timestamp)
    return timestamp.replace(second=0, microsecond=0)


def floor_m30(timestamp):
    timestamp = floor_minute(timestamp)
    return timestamp.replace(minute=0 if timestamp.minute < 30 else 30)


def sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dukascopy_hour_url(symbol, hour_start):
    """Build the public Dukascopy hourly tick-archive URL.

    The archive path uses a zero-based month.  This adapter is accepted for
    research only after the forensic neighbour-calibration gate passes.
    """
    if symbol not in PRICE_SCALES:
        raise ValueError(f"Unsupported V4 research symbol: {symbol}")
    hour_start = parse_utc(hour_start)
    if hour_start != hour_start.replace(minute=0, second=0, microsecond=0):
        raise ValueError(f"Dukascopy request is not hour-aligned: {hour_start}")
    instrument = symbol.replace("/", "")
    return (
        f"{DUKASCOPY_ARCHIVE_ROOT}/{instrument}/"
        f"{hour_start.year:04d}/{hour_start.month - 1:02d}/"
        f"{hour_start.day:02d}/{hour_start.hour:02d}h_ticks.bi5"
    )


def dukascopy_m1_day_url(symbol, day_start, side):
    """Build a candidate public daily M1 archive URL.

    The path and binary adapter remain research candidates until their output
    passes a field-by-field comparison against independently decoded ticks.
    """
    if symbol not in PRICE_SCALES:
        raise ValueError(f"Unsupported V4 research symbol: {symbol}")
    side = str(side).lower()
    if side not in OFFER_SIDES:
        raise ValueError(f"Unsupported Dukascopy offer side: {side}")
    day_start = parse_utc(day_start)
    if day_start != day_start.replace(hour=0, minute=0, second=0, microsecond=0):
        raise ValueError(f"Dukascopy day is not UTC-midnight aligned: {day_start}")
    instrument = symbol.replace("/", "")
    return (
        f"{DUKASCOPY_ARCHIVE_ROOT}/{instrument}/"
        f"{day_start.year:04d}/{day_start.month - 1:02d}/"
        f"{day_start.day:02d}/{side.upper()}_candles_min_1.bi5"
    )


def _download_archive(
    *,
    url,
    label,
    timeout,
    max_attempts,
    retry_backoff_seconds,
    retry_notifier,
):
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    if retry_backoff_seconds < 0:
        raise ValueError("retry_backoff_seconds cannot be negative")

    request = Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": DOWNLOAD_USER_AGENT,
        },
    )

    for attempt in range(1, max_attempts + 1):
        failure = None
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read(), url
        except HTTPError as error:
            if error.code == 404:
                return b"", url
            if error.code not in RETRYABLE_HTTP_CODES:
                raise RuntimeError(f"Dukascopy HTTP {error.code} for {label}") from None
            failure = f"HTTP {error.code}"
        except (URLError, ConnectionResetError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            failure = f"connection error: {reason}"

        if attempt == max_attempts:
            raise RuntimeError(
                f"Dukascopy download failed after {max_attempts} attempts for "
                f"{label}: {failure}"
            ) from None

        delay = min(
            retry_backoff_seconds * (2 ** (attempt - 1)),
            30.0,
        )
        if retry_notifier is not None:
            retry_notifier(attempt, max_attempts, delay, failure)
        if delay:
            time.sleep(delay)

    raise AssertionError("Unreachable Dukascopy download state")


def download_hour(
    symbol,
    hour_start,
    timeout=90,
    *,
    max_attempts=6,
    retry_backoff_seconds=2.0,
    retry_notifier=None,
):
    """Download one hour with bounded retries for transient transport errors."""
    url = dukascopy_hour_url(symbol, hour_start)
    return _download_archive(
        url=url,
        label=f"{symbol} {hour_start}",
        timeout=timeout,
        max_attempts=max_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
        retry_notifier=retry_notifier,
    )


def download_m1_day(
    symbol,
    day_start,
    side,
    timeout=90,
    *,
    max_attempts=6,
    retry_backoff_seconds=2.0,
    retry_notifier=None,
):
    """Download one candidate daily M1 BID or ASK archive."""
    url = dukascopy_m1_day_url(symbol, day_start, side)
    return _download_archive(
        url=url,
        label=f"{symbol} {day_start} {str(side).upper()} M1",
        timeout=timeout,
        max_attempts=max_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
        retry_notifier=retry_notifier,
    )


def decode_bi5_ticks(payload, *, symbol, hour_start):
    """Decode one hourly Dukascopy BI5 tick artifact.

    Each decompressed big-endian record contains millisecond offset, ASK,
    BID, ASK volume, and BID volume.  EUR/USD and GBP/USD use a 1e5 scale.
    """
    if symbol not in PRICE_SCALES:
        raise ValueError(f"Unsupported V4 research symbol: {symbol}")
    hour_start = parse_utc(hour_start)
    if hour_start != hour_start.replace(minute=0, second=0, microsecond=0):
        raise ValueError(f"BI5 hour is not aligned: {hour_start}")
    if not payload:
        return []

    try:
        raw = lzma.decompress(payload)
    except lzma.LZMAError as error:
        raise RuntimeError(
            f"Invalid Dukascopy BI5 compression for {symbol} {hour_start}"
        ) from error

    if len(raw) % TICK_RECORD.size:
        raise RuntimeError(
            f"Truncated Dukascopy BI5 record for {symbol} {hour_start}: "
            f"{len(raw)} bytes"
        )

    scale = PRICE_SCALES[symbol]
    ticks = []
    previous_offset = -1
    for offset in range(0, len(raw), TICK_RECORD.size):
        millis, ask_raw, bid_raw, ask_volume, bid_volume = TICK_RECORD.unpack_from(
            raw,
            offset,
        )
        if millis >= 3_600_000 or millis < previous_offset:
            raise RuntimeError(
                f"Invalid/out-of-order Dukascopy tick offset for "
                f"{symbol} {hour_start}: {millis}"
            )
        previous_offset = millis

        ask = ask_raw / scale
        bid = bid_raw / scale
        values = (ask, bid, float(ask_volume), float(bid_volume))
        if not all(math.isfinite(value) and value >= 0 for value in values):
            raise RuntimeError(
                f"Non-finite Dukascopy tick for {symbol} {hour_start}"
            )
        if bid <= 0 or ask <= 0 or bid > ask:
            raise RuntimeError(
                f"Invalid BID/ASK tick for {symbol} {hour_start}: "
                f"bid={bid} ask={ask}"
            )

        ticks.append(
            {
                "timestamp": hour_start + timedelta(milliseconds=millis),
                "bid": bid,
                "ask": ask,
                "mid": (bid + ask) / 2.0,
                "bid_volume": float(bid_volume),
                "ask_volume": float(ask_volume),
            }
        )
    return ticks


def decode_bi5_m1_candles(payload, *, symbol, day_start, side):
    """Decode a candidate Dukascopy daily BID or ASK M1 artifact.

    The assumed record is UTC-second offset, open, close, low, high integer
    prices, and float volume.  A real-data tick comparison must approve this
    candidate adapter before it can feed multi-year research.
    """
    if symbol not in PRICE_SCALES:
        raise ValueError(f"Unsupported V4 research symbol: {symbol}")
    side = str(side).lower()
    if side not in OFFER_SIDES:
        raise ValueError(f"Unsupported Dukascopy offer side: {side}")
    day_start = parse_utc(day_start)
    if day_start != day_start.replace(hour=0, minute=0, second=0, microsecond=0):
        raise ValueError(f"BI5 candle day is not UTC-midnight aligned: {day_start}")
    if not payload:
        return []

    try:
        raw = lzma.decompress(payload)
    except lzma.LZMAError as error:
        raise RuntimeError(
            f"Invalid Dukascopy M1 BI5 compression for {symbol} {day_start} {side}"
        ) from error

    if len(raw) % CANDLE_RECORD.size:
        raise RuntimeError(
            f"Truncated Dukascopy M1 BI5 record for {symbol} {day_start} {side}: "
            f"{len(raw)} bytes"
        )

    scale = PRICE_SCALES[symbol]
    rows = []
    previous_seconds = -1
    for offset in range(0, len(raw), CANDLE_RECORD.size):
        (
            seconds,
            open_raw,
            close_raw,
            low_raw,
            high_raw,
            volume,
        ) = CANDLE_RECORD.unpack_from(raw, offset)
        if seconds >= 86_400 or seconds % 60 or seconds <= previous_seconds:
            raise RuntimeError(
                f"Invalid/out-of-order Dukascopy M1 offset for "
                f"{symbol} {day_start} {side}: {seconds}"
            )
        previous_seconds = seconds
        ohlc = {
            "open": open_raw / scale,
            "high": high_raw / scale,
            "low": low_raw / scale,
            "close": close_raw / scale,
        }
        values = tuple(ohlc[field] for field in OHLC_FIELDS) + (float(volume),)
        if not all(math.isfinite(value) and value >= 0 for value in values):
            raise RuntimeError(
                f"Invalid Dukascopy M1 value for {symbol} {day_start} {side}"
            )
        if any(ohlc[field] <= 0 for field in OHLC_FIELDS) or invalid_geometry(ohlc):
            raise RuntimeError(
                f"Invalid Dukascopy M1 geometry for {symbol} {day_start} "
                f"{side} at {seconds}"
            )
        rows.append(
            {
                "timestamp": day_start + timedelta(seconds=seconds),
                "side": side,
                "ohlc": ohlc,
                "volume": float(volume),
            }
        )
    return rows


def merge_bid_ask_m1(bid_rows, ask_rows):
    """Merge aligned daily BID/ASK bars without inventing missing minutes."""
    if any(str(row.get("side", "")).lower() != "bid" for row in bid_rows):
        raise RuntimeError("Daily M1 BID collection contains a non-BID row")
    if any(str(row.get("side", "")).lower() != "ask" for row in ask_rows):
        raise RuntimeError("Daily M1 ASK collection contains a non-ASK row")
    bid_by_time = {parse_utc(row["timestamp"]): row for row in bid_rows}
    ask_by_time = {parse_utc(row["timestamp"]): row for row in ask_rows}
    if len(bid_by_time) != len(bid_rows) or len(ask_by_time) != len(ask_rows):
        raise RuntimeError("Duplicate daily M1 BID/ASK timestamp")
    if set(bid_by_time) != set(ask_by_time):
        missing_bid = sorted(set(ask_by_time) - set(bid_by_time))
        missing_ask = sorted(set(bid_by_time) - set(ask_by_time))
        raise RuntimeError(
            f"Daily M1 BID/ASK timestamps differ: "
            f"missing_bid={missing_bid[:5]} missing_ask={missing_ask[:5]}"
        )

    result = []
    for timestamp in sorted(bid_by_time):
        bid = bid_by_time[timestamp]["ohlc"]
        ask = ask_by_time[timestamp]["ohlc"]
        mid = {
            field: (float(bid[field]) + float(ask[field])) / 2.0
            for field in OHLC_FIELDS
        }
        row = {
            "timestamp": timestamp,
            "bid": dict(bid),
            "ask": dict(ask),
            "mid": mid,
            "bid_volume": float(bid_by_time[timestamp]["volume"]),
            "ask_volume": float(ask_by_time[timestamp]["volume"]),
            "source_bar_count": 2,
            "aggregation_policy": "DAILY_M1_BID_ASK_MID_PROXY",
        }
        _validate_sides(row, f"daily M1 {timestamp}")
        result.append(row)
    validate_m1_rows(result)
    return result


def _new_side_ohlc(price):
    return {
        "open": float(price),
        "high": float(price),
        "low": float(price),
        "close": float(price),
    }


def _update_side_ohlc(ohlc, price):
    price = float(price)
    ohlc["high"] = max(ohlc["high"], price)
    ohlc["low"] = min(ohlc["low"], price)
    ohlc["close"] = price


def aggregate_ticks_to_m1(ticks):
    """Aggregate chronological quote ticks to tick-derived BID/ASK/MID M1."""
    rows = OrderedDict()
    previous_time = None

    for tick in ticks:
        timestamp = parse_utc(tick["timestamp"])
        if previous_time is not None and timestamp < previous_time:
            raise RuntimeError(f"Out-of-order tick timestamp: {timestamp}")
        previous_time = timestamp

        bid = float(tick["bid"])
        ask = float(tick["ask"])
        mid = float(tick.get("mid", (bid + ask) / 2.0))
        if not all(math.isfinite(value) and value > 0 for value in (bid, ask, mid)):
            raise RuntimeError(f"Invalid tick price at {timestamp}")
        if bid > ask:
            raise RuntimeError(f"BID exceeds ASK at {timestamp}")

        minute = floor_minute(timestamp)
        row = rows.get(minute)
        if row is None:
            row = {
                "timestamp": minute,
                "bid": _new_side_ohlc(bid),
                "ask": _new_side_ohlc(ask),
                "mid": _new_side_ohlc(mid),
                "tick_count": 0,
            }
            rows[minute] = row
        else:
            _update_side_ohlc(row["bid"], bid)
            _update_side_ohlc(row["ask"], ask)
            _update_side_ohlc(row["mid"], mid)
        row["tick_count"] += 1

    result = list(rows.values())
    validate_m1_rows(result)
    return result


def invalid_geometry(ohlc):
    open_price = float(ohlc["open"])
    high = float(ohlc["high"])
    low = float(ohlc["low"])
    close = float(ohlc["close"])
    return high < max(open_price, low, close) or low > min(
        open_price,
        high,
        close,
    )


def _validate_sides(row, label):
    for side in SIDES:
        ohlc = row[side]
        values = tuple(float(ohlc[field]) for field in OHLC_FIELDS)
        if not all(math.isfinite(value) and value > 0 for value in values):
            raise RuntimeError(f"Invalid {side} price in {label}")
        if invalid_geometry(ohlc):
            raise RuntimeError(f"Invalid {side} OHLC geometry in {label}")

    for field in OHLC_FIELDS:
        if float(row["bid"][field]) > float(row["ask"][field]):
            raise RuntimeError(f"BID exceeds ASK {field} in {label}")


def validate_m1_rows(rows):
    previous = None
    for index, row in enumerate(rows):
        timestamp = parse_utc(row["timestamp"])
        if timestamp.second or timestamp.microsecond:
            raise RuntimeError(f"Unaligned M1 timestamp: {timestamp}")
        if previous is not None and timestamp <= previous:
            raise RuntimeError(f"Duplicate/out-of-order M1 timestamp: {timestamp}")
        previous = timestamp
        tick_count = int(row.get("tick_count", 0))
        source_bar_count = int(row.get("source_bar_count", 0))
        if tick_count <= 0 and source_bar_count <= 0:
            raise RuntimeError(f"Empty M1 candle at {timestamp}")
        _validate_sides(row, f"M1 row {index} {timestamp}")


def _aggregate_side(rows, side):
    return {
        "open": float(rows[0][side]["open"]),
        "high": max(float(row[side]["high"]) for row in rows),
        "low": min(float(row[side]["low"]) for row in rows),
        "close": float(rows[-1][side]["close"]),
    }


def _aggregate_tick_side(ticks, side):
    prices = [
        float(
            tick.get(
                side,
                (float(tick["bid"]) + float(tick["ask"])) / 2.0,
            )
        )
        for tick in ticks
    ]
    return {
        "open": prices[0],
        "high": max(prices),
        "low": min(prices),
        "close": prices[-1],
    }


def aggregate_ticks_to_m30(ticks, *, complete_hours):
    """Build M30 directly from ticks whose hourly artifacts are complete.

    A minute with no quote update is recorded as silent rather than synthesized.
    The M30 remains usable because its OHLC is defined by observed ticks across
    a complete raw hour.  This policy must not be used to fabricate an M1 path.
    """
    normalized_hours = set()
    for value in complete_hours:
        hour = parse_utc(value)
        if hour != hour.replace(minute=0, second=0, microsecond=0):
            raise ValueError(f"Complete raw hour is not aligned: {hour}")
        normalized_hours.add(hour)

    grouped = OrderedDict()
    previous_time = None
    for tick in ticks:
        timestamp = parse_utc(tick["timestamp"])
        if previous_time is not None and timestamp < previous_time:
            raise RuntimeError(f"Out-of-order tick timestamp: {timestamp}")
        previous_time = timestamp

        bid = float(tick["bid"])
        ask = float(tick["ask"])
        mid = float(tick.get("mid", (bid + ask) / 2.0))
        if not all(math.isfinite(value) and value > 0 for value in (bid, ask, mid)):
            raise RuntimeError(f"Invalid tick price at {timestamp}")
        if bid > ask:
            raise RuntimeError(f"BID exceeds ASK at {timestamp}")

        normalized = dict(tick)
        normalized["timestamp"] = timestamp
        normalized["bid"] = bid
        normalized["ask"] = ask
        normalized["mid"] = mid
        grouped.setdefault(floor_m30(timestamp), []).append(normalized)

    result = []
    for bucket, bucket_ticks in grouped.items():
        observed_minutes = {
            floor_minute(tick["timestamp"])
            for tick in bucket_ticks
        }
        expected_minutes = [
            bucket + timedelta(minutes=index)
            for index in range(30)
        ]
        silent_minutes = [
            minute for minute in expected_minutes
            if minute not in observed_minutes
        ]
        source_hour = bucket.replace(minute=0)
        source_complete = source_hour in normalized_hours
        gaps = [
            (
                bucket_ticks[index]["timestamp"]
                - bucket_ticks[index - 1]["timestamp"]
            ).total_seconds()
            for index in range(1, len(bucket_ticks))
        ]

        row = {
            "timestamp": bucket,
            "bid": _aggregate_tick_side(bucket_ticks, "bid"),
            "ask": _aggregate_tick_side(bucket_ticks, "ask"),
            "mid": _aggregate_tick_side(bucket_ticks, "mid"),
            "m1_rows": len(observed_minutes),
            "tick_count": len(bucket_ticks),
            "first_tick_time": bucket_ticks[0]["timestamp"],
            "last_tick_time": bucket_ticks[-1]["timestamp"],
            "max_tick_gap_seconds": max(gaps, default=0.0),
            "missing_minutes": silent_minutes,
            "source_complete": source_complete,
            "quality_status": "USABLE"
            if source_complete
            else "MISSING_RAW_HOUR",
            "aggregation_policy": "DIRECT_TICKS_COMPLETE_HOUR",
        }
        _validate_sides(row, f"direct-tick M30 {bucket}")
        result.append(row)
    return result


def aggregate_m1_to_m30(rows):
    """Aggregate M1 rows to M30 and mark incomplete buckets unusable."""
    validate_m1_rows(rows)
    grouped = OrderedDict()
    for row in rows:
        bucket = floor_m30(row["timestamp"])
        grouped.setdefault(bucket, []).append(row)

    result = []
    for bucket, bucket_rows in grouped.items():
        expected = [bucket + timedelta(minutes=index) for index in range(30)]
        observed = [parse_utc(row["timestamp"]) for row in bucket_rows]
        complete = observed == expected
        gaps = [
            int((observed[index] - observed[index - 1]).total_seconds() // 60)
            for index in range(1, len(observed))
        ]

        row = {
            "timestamp": bucket,
            "bid": _aggregate_side(bucket_rows, "bid"),
            "ask": _aggregate_side(bucket_rows, "ask"),
            "mid": _aggregate_side(bucket_rows, "mid"),
            "m1_rows": len(bucket_rows),
            "tick_count": sum(int(item.get("tick_count", 0)) for item in bucket_rows),
            "source_bar_count": sum(
                int(item.get("source_bar_count", 0))
                for item in bucket_rows
            ),
            "first_m1_time": observed[0],
            "last_m1_time": observed[-1],
            "max_internal_gap_minutes": max(gaps, default=0),
            "source_complete": complete,
            "quality_status": "USABLE" if complete else "MISSING_M1",
        }
        _validate_sides(row, f"M30 {bucket}")
        result.append(row)
    return result


def m30_strategy_rows(rows, *, side="mid"):
    """Convert only usable canonical M30 bars to the existing scanner schema."""
    if side not in SIDES:
        raise ValueError(f"Unknown price side: {side}")
    result = []
    for row in rows:
        if row["quality_status"] != "USABLE":
            continue
        timestamp = parse_utc(row["timestamp"])
        values = row[side]
        result.append(
            {
                "datetime": timestamp.strftime(TIME_FORMAT),
                "_time": timestamp,
                "open": float(values["open"]),
                "high": float(values["high"]),
                "low": float(values["low"]),
                "close": float(values["close"]),
            }
        )
    return result


def write_raw_artifact(path, payload):
    """Write immutable raw bytes, refusing a conflicting existing artifact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = sha256_bytes(payload)
    if path.exists():
        existing = sha256_file(path)
        if existing != digest:
            raise RuntimeError(
                f"Raw artifact hash conflict at {path}: {existing} != {digest}"
            )
        return digest
    path.write_bytes(payload)
    return digest


def write_json_artifact(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        payload,
        indent=2,
        sort_keys=True,
        default=lambda value: value.strftime(TIME_FORMAT)
        if isinstance(value, datetime)
        else value,
    )
    path.write_text(serialized + "\n", encoding="utf-8")
    return sha256_file(path)
