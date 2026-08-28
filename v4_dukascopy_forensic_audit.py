"""Independent Dukascopy forensic audit for invalid Twelve Data M30 candles.

The production SQLite database is opened read-only and fingerprinted before
and after the run.  Only short tick windows around proven-invalid M30 candles
are downloaded.  Results are evidence candidates under /tmp; this script does
not repair, overlay, or materialize any database.
"""

import math
import sqlite3
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import v4_research_data as research_data


DB_PATH = Path("/app/data/v4_history.db")
DB_URI = f"file:{DB_PATH}?mode=ro"
SYMBOL = "EUR/USD"
# Match the V4 backtest's strict read boundary: an M30 candle whose close
# reaches 2026 is not part of the readable research universe.
READ_LIMIT = datetime(2025, 12, 31, 23, 30)
PIP_SIZE = 0.0001
WINDOW_BEFORE = timedelta(minutes=60)
WINDOW_AFTER = timedelta(minutes=90)
REQUEST_INTERVAL_SECONDS = 0.25
OUTPUT_DIR = Path("/tmp/v4_dukascopy_forensic")
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"

# These are data-alignment safety gates, not trading parameters.  A wrong
# archive path/month convention would miss them by hundreds of pips.
MAX_NEIGHBOUR_MEDIAN_PIPS = 5.0
MAX_NEIGHBOUR_P95_PIPS = 10.0


def db_ohlc(row):
    return tuple(float(value) for value in row[1:5])


def invalid_tuple(values):
    open_price, high, low, close = values
    return high < max(open_price, low, close) or low > min(
        open_price,
        high,
        close,
    )


def load_db_rows(connection):
    return connection.execute(
        """
        SELECT datetime, open, high, low, close
        FROM candles_30m
        WHERE symbol = ? AND datetime < ?
        ORDER BY datetime
        """,
        (SYMBOL, READ_LIMIT.strftime(research_data.TIME_FORMAT)),
    ).fetchall()


def required_hours(timestamps):
    result = set()
    for timestamp in timestamps:
        start = (timestamp - WINDOW_BEFORE).replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        end = timestamp + WINDOW_AFTER
        current = start
        while current < end:
            result.add(current)
            current += timedelta(hours=1)
    return sorted(result)


def raw_path(hour):
    return (
        OUTPUT_DIR
        / "raw"
        / SYMBOL.replace("/", "")
        / f"{hour:%Y}"
        / f"{hour:%m}"
        / f"{hour:%d}"
        / f"{hour:%H}h_ticks.bi5"
    )


def fetch_or_read_hour(hour):
    path = raw_path(hour)
    url = research_data.dukascopy_hour_url(SYMBOL, hour)
    if path.exists():
        payload = path.read_bytes()
        source = "CACHE"
    else:
        payload, returned_url = research_data.download_hour(SYMBOL, hour)
        if returned_url != url:
            raise RuntimeError("Dukascopy URL mismatch")
        if not payload:
            raise RuntimeError(
                f"Empty/missing Dukascopy hour required by a target window: {hour}"
            )
        research_data.write_raw_artifact(path, payload)
        source = "NETWORK"
    if not payload:
        raise RuntimeError(
            f"Cached Dukascopy hour is empty but required by a target window: {hour}"
        )
    return payload, url, source


def percentile(values, probability):
    if not values:
        raise ValueError("Cannot calculate percentile of empty values")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def max_difference_pips(left, right):
    return max(
        abs(float(left[index]) - float(right[index]))
        for index in range(4)
    ) / PIP_SIZE


def ohlc_tuple(row, side="mid"):
    values = row[side]
    return tuple(float(values[field]) for field in research_data.OHLC_FIELDS)


def main():
    if not DB_PATH.is_file():
        raise RuntimeError(f"V4 database is missing: {DB_PATH}")

    db_hash_before = research_data.sha256_file(DB_PATH)
    connection = sqlite3.connect(DB_URI, uri=True)
    connection.execute("PRAGMA query_only = ON")
    try:
        db_rows = load_db_rows(connection)
    finally:
        connection.close()

    db_by_time = {
        research_data.parse_utc(row[0]): row
        for row in db_rows
    }
    invalid_rows = [row for row in db_rows if invalid_tuple(db_ohlc(row))]
    invalid_times = [research_data.parse_utc(row[0]) for row in invalid_rows]

    print("=" * 118)
    print("V4 DUKASCOPY FORENSIC AUDIT | TARGETED TICKS | DATABASE READ-ONLY")
    print("=" * 118)
    print(
        f"{SYMBOL} | DBRows={len(db_rows)} | "
        f"InvalidM30={len(invalid_rows)} | DB_SHA256={db_hash_before}"
    )

    if not invalid_rows:
        print("NO_INVALID_M30_TO_AUDIT")
        print("DATABASE_UNCHANGED")
        print("V4_DUKASCOPY_FORENSIC_AUDIT_OK")
        return

    hours = required_hours(invalid_times)
    print(f"UNIQUE_DUKASCOPY_HOURS={len(hours)}")

    ticks = []
    artifacts = []
    last_network_request = None
    for position, hour in enumerate(hours, start=1):
        path = raw_path(hour)
        if not path.exists() and last_network_request is not None:
            elapsed = time.monotonic() - last_network_request
            wait_seconds = max(0.0, REQUEST_INTERVAL_SECONDS - elapsed)
            if wait_seconds:
                time.sleep(wait_seconds)

        if not path.exists():
            last_network_request = time.monotonic()
        payload, url, source = fetch_or_read_hour(hour)
        hour_ticks = research_data.decode_bi5_ticks(
            payload,
            symbol=SYMBOL,
            hour_start=hour,
        )
        ticks.extend(hour_ticks)
        artifacts.append(
            {
                "hour_utc": hour,
                "url": url,
                "path": str(path),
                "sha256": research_data.sha256_bytes(payload),
                "bytes": len(payload),
                "ticks": len(hour_ticks),
                "source": source,
            }
        )
        print(
            f"HOUR {position:03d}/{len(hours):03d} | {hour} | "
            f"Ticks={len(hour_ticks):6d} | Bytes={len(payload):7d} | {source}",
            flush=True,
        )

    ticks.sort(key=lambda row: row["timestamp"])
    m1_rows = research_data.aggregate_ticks_to_m1(ticks)
    m30_rows = research_data.aggregate_m1_to_m30(m1_rows)
    m30_by_time = {row["timestamp"]: row for row in m30_rows}

    print()
    print("TARGET RECONSTRUCTION")
    print("=" * 118)

    targets = []
    neighbour_differences = []
    reconstructed_count = 0

    for db_row in invalid_rows:
        timestamp = research_data.parse_utc(db_row[0])
        reconstructed = m30_by_time.get(timestamp)
        usable = (
            reconstructed is not None
            and reconstructed["quality_status"] == "USABLE"
        )
        candidate = ohlc_tuple(reconstructed) if usable else None
        candidate_valid = candidate is not None and not invalid_tuple(candidate)
        if candidate_valid:
            reconstructed_count += 1

        local_neighbours = []
        for offset in (-60, -30, 30, 60):
            neighbour_time = timestamp + timedelta(minutes=offset)
            neighbour_db = db_by_time.get(neighbour_time)
            neighbour_ref = m30_by_time.get(neighbour_time)
            if neighbour_db is None or neighbour_ref is None:
                continue
            if invalid_tuple(db_ohlc(neighbour_db)):
                continue
            if neighbour_ref["quality_status"] != "USABLE":
                continue
            difference = max_difference_pips(
                db_ohlc(neighbour_db),
                ohlc_tuple(neighbour_ref),
            )
            local_neighbours.append(difference)
            neighbour_differences.append(difference)

        target_difference = (
            max_difference_pips(db_ohlc(db_row), candidate)
            if candidate is not None
            else None
        )
        targets.append(
            {
                "timestamp": timestamp,
                "twelve_ohlc": db_ohlc(db_row),
                "dukascopy_mid_ohlc": candidate,
                "dukascopy_bid_ohlc": ohlc_tuple(reconstructed, "bid")
                if usable
                else None,
                "dukascopy_ask_ohlc": ohlc_tuple(reconstructed, "ask")
                if usable
                else None,
                "source_complete": usable,
                "candidate_geometry_valid": candidate_valid,
                "target_difference_pips": target_difference,
                "neighbour_difference_pips": local_neighbours,
                "status": "INDEPENDENT_CANDIDATE"
                if candidate_valid
                else "UNRESOLVED",
            }
        )
        print(
            f"{timestamp} | Twelve={db_ohlc(db_row)} | "
            f"DukascopyMID={candidate} | Complete={usable} | "
            f"Valid={candidate_valid} | DiffPips={target_difference} | "
            f"NeighbourPips={local_neighbours}"
        )

    if not neighbour_differences:
        raise RuntimeError("No healthy neighbouring candles for provider alignment")
    neighbour_median = statistics.median(neighbour_differences)
    neighbour_p95 = percentile(neighbour_differences, 0.95)
    neighbour_mad = statistics.median(
        abs(value - neighbour_median) for value in neighbour_differences
    )

    db_hash_after = research_data.sha256_file(DB_PATH)
    manifest = {
        "schema_version": 1,
        "audit": "V4_DUKASCOPY_TARGETED_FORENSIC",
        "created_at_utc": datetime.now(timezone.utc).strftime(
            research_data.TIME_FORMAT
        ),
        "provider": "Dukascopy public datafeed archive",
        "symbol": SYMBOL,
        "price_source": "tick-derived BID/ASK/MID",
        "database": {
            "path": str(DB_PATH),
            "mode": "ro + query_only",
            "sha256_before": db_hash_before,
            "sha256_after": db_hash_after,
        },
        "counts": {
            "invalid_twelve_m30": len(invalid_rows),
            "unique_hours": len(hours),
            "ticks": len(ticks),
            "m1": len(m1_rows),
            "m30": len(m30_rows),
            "reconstructed_valid_targets": reconstructed_count,
            "healthy_neighbour_comparisons": len(neighbour_differences),
        },
        "alignment": {
            "median_max_ohlc_difference_pips": neighbour_median,
            "p95_max_ohlc_difference_pips": neighbour_p95,
            "mad_pips": neighbour_mad,
            "max_allowed_median_pips": MAX_NEIGHBOUR_MEDIAN_PIPS,
            "max_allowed_p95_pips": MAX_NEIGHBOUR_P95_PIPS,
        },
        "raw_artifacts": artifacts,
        "targets": targets,
        "repair_authorized": False,
    }
    manifest_hash = research_data.write_json_artifact(MANIFEST_PATH, manifest)

    print()
    print("FORENSIC SUMMARY")
    print("=" * 118)
    print(f"RECONSTRUCTED_VALID_TARGETS={reconstructed_count}/{len(invalid_rows)}")
    print(f"HEALTHY_NEIGHBOUR_COMPARISONS={len(neighbour_differences)}")
    print(f"NEIGHBOUR_MEDIAN_MAX_DIFF_PIPS={neighbour_median:.3f}")
    print(f"NEIGHBOUR_P95_MAX_DIFF_PIPS={neighbour_p95:.3f}")
    print(f"NEIGHBOUR_MAD_PIPS={neighbour_mad:.3f}")
    print(f"MANIFEST={MANIFEST_PATH}")
    print(f"MANIFEST_SHA256={manifest_hash}")
    print("REPAIR_AUTHORIZED=False")

    if db_hash_after != db_hash_before:
        raise RuntimeError("Production V4 database changed during forensic audit")
    print("DATABASE_UNCHANGED")

    if reconstructed_count != len(invalid_rows):
        raise RuntimeError(
            "Not every invalid Twelve M30 has a complete valid Dukascopy reconstruction"
        )
    if neighbour_median > MAX_NEIGHBOUR_MEDIAN_PIPS:
        raise RuntimeError(
            f"Dukascopy alignment median is too large: {neighbour_median:.3f} pips"
        )
    if neighbour_p95 > MAX_NEIGHBOUR_P95_PIPS:
        raise RuntimeError(
            f"Dukascopy alignment p95 is too large: {neighbour_p95:.3f} pips"
        )

    print("V4_DUKASCOPY_FORENSIC_AUDIT_OK")


if __name__ == "__main__":
    main()
