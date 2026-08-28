"""Research-only Dukascopy tick normalization and deterministic aggregation.

This module deliberately has no production storage, Telegram, or live imports.
It can decode Dukascopy's public hourly BI5 tick artifacts, derives BID, ASK,
and tick-midpoint M1 bars, and then aggregates only complete M1 sequences to
usable M30 bars.  Missing minutes are flagged; prices are never forward-filled.
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
SIDES = ("bid", "ask", "mid")
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
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    if retry_backoff_seconds < 0:
        raise ValueError("retry_backoff_seconds cannot be negative")

    url = dukascopy_hour_url(symbol, hour_start)
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
                raise RuntimeError(
                    f"Dukascopy HTTP {error.code} for {symbol} {hour_start}"
                ) from None
            failure = f"HTTP {error.code}"
        except (URLError, ConnectionResetError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            failure = f"connection error: {reason}"

        if attempt == max_attempts:
            raise RuntimeError(
                f"Dukascopy download failed after {max_attempts} attempts for "
                f"{symbol} {hour_start}: {failure}"
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
        if int(row["tick_count"]) <= 0:
            raise RuntimeError(f"Empty M1 candle at {timestamp}")
        _validate_sides(row, f"M1 row {index} {timestamp}")


def _aggregate_side(rows, side):
    return {
        "open": float(rows[0][side]["open"]),
        "high": max(float(row[side]["high"]) for row in rows),
        "low": min(float(row[side]["low"]) for row in rows),
        "close": float(rows[-1][side]["close"]),
    }


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
            "tick_count": sum(int(item["tick_count"]) for item in bucket_rows),
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
