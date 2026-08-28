"""Read-only V4 history integrity audit with targeted Twelve Data comparison.

The production history database is never modified.  Invalid M30 rows are
identified locally, then only their calendar months are re-fetched from
Twelve Data at a deliberately low request rate.  Reference responses are
cached under /tmp for a later, separately reviewed repair-copy step.
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


DB_URI = "file:/app/data/v4_history.db?mode=ro"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
READ_LIMIT = "2025-12-31 23:30:00"
SYMBOLS = ("EUR/USD", "GBP/USD")
PIP_SIZE = 0.0001

API_URL = "https://api.twelvedata.com/time_series"
API_INTERVAL = "30min"
REQUEST_INTERVAL_SECONDS = 15.0
MAX_ATTEMPTS = 3
RATE_LIMIT_WAIT_SECONDS = 70.0

CACHE_PATH = Path("/tmp/v4_history_integrity_refetch.json")


def _ohlc(row):
    return tuple(float(value) for value in row[1:5])


def invalid_geometry(values):
    open_price, high, low, close = values
    return high < max(open_price, low, close) or low > min(
        open_price,
        high,
        close,
    )


def month_bounds(month):
    start = datetime.strptime(f"{month}-01 00:00:00", TIME_FORMAT)
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1)
    else:
        next_month = start.replace(month=start.month + 1)
    end = next_month - timedelta(seconds=1)
    return start.strftime(TIME_FORMAT), end.strftime(TIME_FORMAT)


def normalize_api_values(values):
    result = {}
    for item in values:
        timestamp = datetime.strptime(item["datetime"], TIME_FORMAT).strftime(
            TIME_FORMAT
        )
        result[timestamp] = (
            float(item["open"]),
            float(item["high"]),
            float(item["low"]),
            float(item["close"]),
        )
    return result


def load_m30(connection, symbol):
    return connection.execute(
        """
        SELECT datetime, open, high, low, close
        FROM candles_30m
        WHERE symbol = ? AND datetime < ?
        ORDER BY datetime
        """,
        (symbol, READ_LIMIT),
    ).fetchall()


def fetch_month(api_key, symbol, month):
    start_date, end_date = month_bounds(month)
    params = {
        "symbol": symbol,
        "interval": API_INTERVAL,
        "start_date": start_date,
        "end_date": end_date,
        "timezone": "UTC",
        "apikey": api_key,
    }
    url = f"{API_URL}?{urlencode(params)}"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urlopen(url, timeout=90) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code == 429:
                if attempt == MAX_ATTEMPTS:
                    raise RuntimeError(
                        f"Twelve Data rate limit persisted for {symbol} {month}"
                    ) from None
                print(
                    f"RATE_LIMIT {symbol} {month} | waiting "
                    f"{RATE_LIMIT_WAIT_SECONDS:.0f}s",
                    flush=True,
                )
                time.sleep(RATE_LIMIT_WAIT_SECONDS)
                continue
            raise RuntimeError(
                f"Twelve Data HTTP {error.code} for {symbol} {month}"
            ) from None
        except URLError:
            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(
                    f"Twelve Data connection failed for {symbol} {month}"
                ) from None
            time.sleep(REQUEST_INTERVAL_SECONDS)
            continue

        if payload.get("status") == "error":
            code = payload.get("code", "unknown")
            message = payload.get("message", "unknown API error")
            raise RuntimeError(
                f"Twelve Data error {code} for {symbol} {month}: {message}"
            )

        values = payload.get("values")
        if not values:
            raise RuntimeError(f"No Twelve Data values for {symbol} {month}")
        return normalize_api_values(values)

    raise RuntimeError("unreachable API retry state")


def compare_month(db_rows, reference_rows, months):
    missing_reference = 0
    material_mismatches = 0
    over_one_pip = 0
    largest = []

    for row in db_rows:
        timestamp = row[0]
        if timestamp[:7] not in months:
            continue

        reference = reference_rows.get(timestamp)
        if reference is None:
            missing_reference += 1
            continue

        actual = _ohlc(row)
        difference_pips = max(
            abs(actual_value - reference_value)
            for actual_value, reference_value in zip(actual, reference)
        ) / PIP_SIZE

        if difference_pips > 0.1:
            material_mismatches += 1
        if difference_pips > 1.0:
            over_one_pip += 1

        if difference_pips > 0.1:
            largest.append((difference_pips, timestamp, actual, reference))

    largest.sort(reverse=True)
    return {
        "missing_reference": missing_reference,
        "material_mismatches": material_mismatches,
        "over_one_pip": over_one_pip,
        "largest": largest,
    }


def main():
    api_key = os.getenv("TWELVE_DATA_RESEARCH_API_KEY") or os.getenv(
        "TWELVE_DATA_API_KEY"
    )
    if not api_key:
        raise RuntimeError("TWELVE_DATA_API_KEY is not configured")

    connection = sqlite3.connect(DB_URI, uri=True)
    connection.execute("PRAGMA query_only = ON")

    try:
        db_rows = {symbol: load_m30(connection, symbol) for symbol in SYMBOLS}
    finally:
        connection.close()

    invalid = {}
    affected = []

    print("=" * 110)
    print("V4 HISTORY INTEGRITY AUDIT | DATABASE READ-ONLY | API REFERENCE ONLY")
    print("=" * 110)

    for symbol in SYMBOLS:
        invalid[symbol] = [
            row for row in db_rows[symbol] if invalid_geometry(_ohlc(row))
        ]
        months = sorted({row[0][:7] for row in invalid[symbol]})
        print(
            f"{symbol} | Rows={len(db_rows[symbol])} | "
            f"Invalid={len(invalid[symbol])} | Months={months}"
        )
        affected.extend((symbol, month) for month in months)

    if not affected:
        print("NO_INVALID_GEOMETRY")
        print("V4_HISTORY_INTEGRITY_AUDIT_OK")
        return

    fetched = {symbol: {} for symbol in SYMBOLS}
    last_request_started = None

    for position, (symbol, month) in enumerate(affected, start=1):
        if last_request_started is not None:
            elapsed = time.monotonic() - last_request_started
            wait_seconds = max(0.0, REQUEST_INTERVAL_SECONDS - elapsed)
            if wait_seconds:
                time.sleep(wait_seconds)

        print(
            f"FETCH {position}/{len(affected)} | {symbol} | {month}",
            flush=True,
        )
        last_request_started = time.monotonic()
        month_rows = fetch_month(api_key, symbol, month)
        fetched[symbol][month] = month_rows
        print(f"FETCHED {symbol} {month} | Rows={len(month_rows)}", flush=True)

    cache_payload = {
        "source": "Twelve Data",
        "fetched_at_utc": datetime.now(timezone.utc).strftime(TIME_FORMAT),
        "interval": API_INTERVAL,
        "read_limit": READ_LIMIT,
        "symbols": {
            symbol: {
                month: {
                    timestamp: list(values)
                    for timestamp, values in sorted(month_rows.items())
                }
                for month, month_rows in fetched[symbol].items()
            }
            for symbol in SYMBOLS
        },
    }
    CACHE_PATH.write_text(
        json.dumps(cache_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"REFERENCE_CACHE={CACHE_PATH}")

    print()
    print("INVALID ROW COMPARISON")
    print("=" * 110)

    for symbol in SYMBOLS:
        reference_rows = {
            timestamp: values
            for month_rows in fetched[symbol].values()
            for timestamp, values in month_rows.items()
        }
        months = set(fetched[symbol])

        for row in invalid[symbol]:
            timestamp = row[0]
            reference = reference_rows.get(timestamp)
            print(
                f"{symbol} | {timestamp} | DB={_ohlc(row)} | "
                f"API={reference} | "
                f"API_VALID={reference is not None and not invalid_geometry(reference)}"
            )

        if not months:
            continue

        comparison = compare_month(db_rows[symbol], reference_rows, months)
        print()
        print(
            f"{symbol} AFFECTED-MONTH COMPARISON | "
            f"MissingAPI={comparison['missing_reference']} | "
            f">0.1pip={comparison['material_mismatches']} | "
            f">1pip={comparison['over_one_pip']}"
        )
        for difference, timestamp, actual, reference in comparison["largest"][:40]:
            print(
                f"DIFF {difference:.3f} pips | {timestamp} | "
                f"DB={actual} | API={reference}"
            )

    print()
    print("DATABASE_UNCHANGED")
    print("V4_HISTORY_INTEGRITY_AUDIT_OK")


if __name__ == "__main__":
    main()
