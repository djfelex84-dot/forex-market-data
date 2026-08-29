"""Build an isolated, resumable Dukascopy M1 dataset for V4 TRAIN only.

The builder downloads daily BID/ASK archives one side at a time, stores raw
artifacts immutably under /tmp, and materializes normalized rows in a separate
research SQLite database.  It never opens production storage and has a hard
exclusive boundary at 2025-01-01.  December 2020 is context only; strategy
outcomes remain limited to TRAIN 2021-2024.
"""

import fcntl
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import v4_research_data as research_data
from v4_event_strategy import TRAIN_END, TRAIN_START


SYMBOLS = ("EUR/USD", "GBP/USD")
CONTEXT_START = datetime(2020, 12, 1)
END_EXCLUSIVE = TRAIN_END
OUTPUT_DIR = Path("/tmp/v4_dukascopy_train")
RAW_DIR = OUTPUT_DIR / "raw"
DATABASE_PATH = OUTPUT_DIR / "v4_train_m1.sqlite3"
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"
ADAPTER_AUDIT_CACHE = Path("/tmp/v4_dukascopy_daily_m1_audit/raw")
REQUEST_INTERVAL_SECONDS = 8.0
EXHAUSTED_REQUEST_COOLDOWN_SECONDS = 30.0
CHECKPOINT_EVERY_DAYS = 25
NORMALIZATION_VERSION = 1
EXPECTED_DAILY_M1_ROWS = 1_440
ADAPTER_GATE_COMMIT = "6e75394c46bfe849ffda87b39349fce6168e728f"
ADAPTER_GATE_MANIFEST_SHA256 = (
    "d781328db867d7da67e1088d0cab36c85d885e26522f146a36f9b463abc89160"
)


class RequiredSourceGapError(RuntimeError):
    """A required non-Saturday BID/ASK daily artifact is unavailable."""


def research_days():
    if CONTEXT_START >= TRAIN_START or END_EXCLUSIVE != datetime(2025, 1, 1):
        raise RuntimeError("Unexpected V4 TRAIN boundary configuration")
    result = []
    current = CONTEXT_START
    while current < END_EXCLUSIVE:
        result.append(current)
        current += timedelta(days=1)
    if not result or result[-1] >= END_EXCLUSIVE:
        raise RuntimeError("V4 builder exposed the locked 2025 boundary")
    return result


def acquire_run_lock():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "run.lock"
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError(
            f"Another V4 TRAIN builder already holds {path}"
        ) from None
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()}\n")
    handle.flush()
    return handle


def raw_path(symbol, day_start, side):
    return (
        RAW_DIR
        / symbol.replace("/", "")
        / f"{day_start:%Y}"
        / f"{day_start:%m}"
        / f"{day_start:%d}"
        / f"{side.upper()}_candles_min_1.bi5"
    )


def adapter_seed_path(symbol, day_start, side):
    return (
        ADAPTER_AUDIT_CACHE
        / symbol.replace("/", "")
        / f"{day_start:%Y}"
        / f"{day_start:%m}"
        / f"{day_start:%d}"
        / f"{side.upper()}_candles_min_1.bi5"
    )


def _metadata_values():
    return {
        "schema_version": "1",
        "normalization_version": str(NORMALIZATION_VERSION),
        "context_start_utc": CONTEXT_START.strftime(research_data.TIME_FORMAT),
        "train_start_utc": TRAIN_START.strftime(research_data.TIME_FORMAT),
        "end_exclusive_utc": END_EXCLUSIVE.strftime(research_data.TIME_FORMAT),
        "validation_2025_locked": "true",
        "symbols": json.dumps(SYMBOLS),
        "adapter_gate_commit": ADAPTER_GATE_COMMIT,
        "adapter_gate_manifest_sha256": ADAPTER_GATE_MANIFEST_SHA256,
        "production_database_opened": "false",
    }


def open_database(path=None):
    path = Path(DATABASE_PATH if path is None else path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS daily_artifacts (
            symbol TEXT NOT NULL,
            day_utc TEXT NOT NULL,
            side TEXT NOT NULL,
            url TEXT NOT NULL,
            path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            bytes INTEGER NOT NULL,
            decoded_rows INTEGER NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (symbol, day_utc, side)
        );

        CREATE TABLE IF NOT EXISTS processed_days (
            symbol TEXT NOT NULL,
            day_utc TEXT NOT NULL,
            bid_sha256 TEXT,
            ask_sha256 TEXT,
            status TEXT NOT NULL,
            source_rows INTEGER NOT NULL,
            observed_rows INTEGER NOT NULL,
            filler_rows INTEGER NOT NULL,
            zero_volume_price_change_rows INTEGER NOT NULL,
            normalization_version INTEGER NOT NULL,
            PRIMARY KEY (symbol, day_utc)
        );

        CREATE TABLE IF NOT EXISTS m1_bars (
            symbol TEXT NOT NULL,
            datetime TEXT NOT NULL,
            bid_open REAL NOT NULL,
            bid_high REAL NOT NULL,
            bid_low REAL NOT NULL,
            bid_close REAL NOT NULL,
            ask_open REAL NOT NULL,
            ask_high REAL NOT NULL,
            ask_low REAL NOT NULL,
            ask_close REAL NOT NULL,
            mid_open REAL NOT NULL,
            mid_high REAL NOT NULL,
            mid_low REAL NOT NULL,
            mid_close REAL NOT NULL,
            bid_volume REAL NOT NULL,
            ask_volume REAL NOT NULL,
            quality_status TEXT NOT NULL,
            PRIMARY KEY (symbol, datetime)
        );

        CREATE TABLE IF NOT EXISTS m1_gaps (
            symbol TEXT NOT NULL,
            datetime TEXT NOT NULL,
            reason TEXT NOT NULL,
            bid_open REAL NOT NULL,
            ask_open REAL NOT NULL,
            PRIMARY KEY (symbol, datetime)
        );

        CREATE INDEX IF NOT EXISTS idx_m1_bars_datetime
        ON m1_bars(datetime);
        """
    )
    expected = _metadata_values()
    existing = dict(connection.execute("SELECT key, value FROM metadata"))
    for key, value in expected.items():
        if key in existing and existing[key] != value:
            connection.close()
            raise RuntimeError(
                f"Research database metadata mismatch for {key}: "
                f"{existing[key]} != {value}"
            )
        connection.execute(
            "INSERT OR IGNORE INTO metadata(key, value) VALUES (?, ?)",
            (key, value),
        )
    connection.commit()
    return connection


def fetch_or_read_side(symbol, day_start, side):
    day_start = research_data.parse_utc(day_start)
    if not CONTEXT_START <= day_start < END_EXCLUSIVE:
        raise RuntimeError(
            f"Attempted to read outside locked V4 TRAIN dataset: {day_start}"
        )
    path = raw_path(symbol, day_start, side)
    url = research_data.dukascopy_m1_day_url(symbol, day_start, side)
    if path.is_file() and path.stat().st_size > 0:
        return path.read_bytes(), url, "CACHE"
    if path.is_file():
        # Older interrupted audit revisions could persist a zero-byte 404
        # marker.  It is not a BI5 artifact and must not block a later retry.
        path.unlink()

    seed = adapter_seed_path(symbol, day_start, side)
    if seed.is_file() and seed.stat().st_size > 0:
        payload = seed.read_bytes()
        research_data.write_raw_artifact(path, payload)
        return payload, url, "ADAPTER_AUDIT_CACHE"

    def retry_notice(attempt, max_attempts, delay, reason):
        print(
            f"RETRY {attempt}/{max_attempts} | {symbol} | "
            f"{day_start:%Y-%m-%d} {side.upper()} | "
            f"Wait={delay:.1f}s | {reason}",
            flush=True,
        )

    payload, returned_url = research_data.download_m1_day(
        symbol,
        day_start,
        side,
        retry_notifier=retry_notice,
    )
    if returned_url != url:
        raise RuntimeError("Dukascopy daily URL mismatch")
    if payload:
        research_data.write_raw_artifact(path, payload)
    return payload, url, "NETWORK" if payload else "NETWORK_404"


def _artifact_record(symbol, day_start, side, payload, url, path, source, rows):
    return (
        symbol,
        day_start.strftime("%Y-%m-%d"),
        side,
        url,
        str(path),
        research_data.sha256_bytes(payload),
        len(payload),
        len(rows),
        source,
    )


def _existing_processed_day(connection, symbol, day_start):
    return connection.execute(
        """
        SELECT bid_sha256, ask_sha256, status, normalization_version,
               source_rows, observed_rows, filler_rows,
               zero_volume_price_change_rows
        FROM processed_days
        WHERE symbol = ? AND day_utc = ?
        """,
        (symbol, day_start.strftime("%Y-%m-%d")),
    ).fetchone()


def record_saturday(connection, symbol, day_start):
    day_start = research_data.parse_utc(day_start)
    if not CONTEXT_START <= day_start < END_EXCLUSIVE:
        raise RuntimeError(
            f"Attempted to record outside locked V4 TRAIN dataset: {day_start}"
        )
    existing = _existing_processed_day(connection, symbol, day_start)
    if existing is not None:
        if existing[:4] != (
            None,
            None,
            "SATURDAY_CLOSED",
            NORMALIZATION_VERSION,
        ):
            raise RuntimeError(
                f"Saturday provenance mismatch: {symbol} {day_start}"
            )
        return "DB_CACHE"
    connection.execute(
        """
        INSERT INTO processed_days(
            symbol, day_utc, bid_sha256, ask_sha256, status,
            source_rows, observed_rows, filler_rows,
            zero_volume_price_change_rows, normalization_version
        ) VALUES (?, ?, NULL, NULL, 'SATURDAY_CLOSED', 0, 0, 0, 0, ?)
        """,
        (
            symbol,
            day_start.strftime("%Y-%m-%d"),
            NORMALIZATION_VERSION,
        ),
    )
    connection.commit()
    return "SATURDAY_CLOSED"


def validate_daily_grid(rows, *, symbol, day_start, side):
    """Reject truncated, shifted, duplicated, or cross-day daily archives."""
    if len(rows) != EXPECTED_DAILY_M1_ROWS:
        raise RuntimeError(
            f"Incomplete Dukascopy daily M1 grid for {symbol} {day_start:%Y-%m-%d} "
            f"{side.upper()}: {len(rows)}/{EXPECTED_DAILY_M1_ROWS} rows"
        )
    for minute, row in enumerate(rows):
        expected = day_start + timedelta(minutes=minute)
        actual = research_data.parse_utc(row["timestamp"])
        if actual != expected:
            raise RuntimeError(
                f"Misaligned Dukascopy daily M1 grid for {symbol} "
                f"{day_start:%Y-%m-%d} {side.upper()} at row {minute}: "
                f"{actual} != {expected}"
            )


def materialize_day(
    connection,
    *,
    symbol,
    day_start,
    bid_payload,
    ask_payload,
    bid_url,
    ask_url,
    bid_source,
    ask_source,
):
    day_start = research_data.parse_utc(day_start)
    if symbol not in SYMBOLS:
        raise RuntimeError(f"Unsupported V4 TRAIN symbol: {symbol}")
    if not CONTEXT_START <= day_start < END_EXCLUSIVE:
        raise RuntimeError(
            f"Attempted to materialize outside locked V4 TRAIN dataset: "
            f"{day_start}"
        )
    if not bid_payload or not ask_payload:
        raise RequiredSourceGapError(
            f"Required BID/ASK day is missing: {symbol} {day_start:%Y-%m-%d} "
            f"bid_bytes={len(bid_payload)} ask_bytes={len(ask_payload)}"
        )
    bid_hash = research_data.sha256_bytes(bid_payload)
    ask_hash = research_data.sha256_bytes(ask_payload)
    existing = _existing_processed_day(connection, symbol, day_start)
    if existing is not None:
        if existing[:4] != (
            bid_hash,
            ask_hash,
            "MATERIALIZED",
            NORMALIZATION_VERSION,
        ):
            raise RuntimeError(
                f"Processed-day provenance mismatch: {symbol} {day_start}"
            )
        return {
            "status": "DB_CACHE",
            "source_rows": existing[4],
            "observed_rows": existing[5],
            "filler_rows": existing[6],
            "zero_volume_price_change_rows": existing[7],
        }

    bid_rows = research_data.decode_bi5_m1_candles(
        bid_payload,
        symbol=symbol,
        day_start=day_start,
        side="bid",
    )
    ask_rows = research_data.decode_bi5_m1_candles(
        ask_payload,
        symbol=symbol,
        day_start=day_start,
        side="ask",
    )
    validate_daily_grid(
        bid_rows,
        symbol=symbol,
        day_start=day_start,
        side="bid",
    )
    validate_daily_grid(
        ask_rows,
        symbol=symbol,
        day_start=day_start,
        side="ask",
    )
    merged = research_data.merge_bid_ask_m1(
        bid_rows,
        ask_rows,
        include_zero_volume_fillers=True,
    )
    observed = [row for row in merged if row["source_observed"]]
    fillers = [row for row in merged if not row["source_observed"]]
    zero_price_change = [
        row
        for row in observed
        if row["quality_status"] == "OBSERVED_ZERO_VOLUME_PRICE_CHANGE"
    ]

    bars = []
    for row in observed:
        values = [symbol, row["timestamp"].strftime(research_data.TIME_FORMAT)]
        for side in research_data.SIDES:
            values.extend(
                float(row[side][field])
                for field in research_data.OHLC_FIELDS
            )
        values.extend(
            [
                float(row["bid_volume"]),
                float(row["ask_volume"]),
                row["quality_status"],
            ]
        )
        bars.append(tuple(values))
    gaps = [
        (
            symbol,
            row["timestamp"].strftime(research_data.TIME_FORMAT),
            row["quality_status"],
            float(row["bid"]["open"]),
            float(row["ask"]["open"]),
        )
        for row in fillers
    ]

    day_text = day_start.strftime("%Y-%m-%d")
    artifacts = [
        _artifact_record(
            symbol,
            day_start,
            "bid",
            bid_payload,
            bid_url,
            raw_path(symbol, day_start, "bid"),
            bid_source,
            bid_rows,
        ),
        _artifact_record(
            symbol,
            day_start,
            "ask",
            ask_payload,
            ask_url,
            raw_path(symbol, day_start, "ask"),
            ask_source,
            ask_rows,
        ),
    ]
    with connection:
        connection.executemany(
            """
            INSERT INTO daily_artifacts(
                symbol, day_utc, side, url, path, sha256, bytes,
                decoded_rows, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            artifacts,
        )
        connection.executemany(
            """
            INSERT INTO m1_bars(
                symbol, datetime,
                bid_open, bid_high, bid_low, bid_close,
                ask_open, ask_high, ask_low, ask_close,
                mid_open, mid_high, mid_low, mid_close,
                bid_volume, ask_volume, quality_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            bars,
        )
        connection.executemany(
            """
            INSERT INTO m1_gaps(
                symbol, datetime, reason, bid_open, ask_open
            ) VALUES (?, ?, ?, ?, ?)
            """,
            gaps,
        )
        connection.execute(
            """
            INSERT INTO processed_days(
                symbol, day_utc, bid_sha256, ask_sha256, status,
                source_rows, observed_rows, filler_rows,
                zero_volume_price_change_rows, normalization_version
            ) VALUES (?, ?, ?, ?, 'MATERIALIZED', ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                day_text,
                bid_hash,
                ask_hash,
                len(merged),
                len(observed),
                len(fillers),
                len(zero_price_change),
                NORMALIZATION_VERSION,
            ),
        )
    return {
        "status": "MATERIALIZED",
        "source_rows": len(merged),
        "observed_rows": len(observed),
        "filler_rows": len(fillers),
        "zero_volume_price_change_rows": len(zero_price_change),
    }


def progress_summary(connection):
    counts = dict(
        connection.execute(
            "SELECT status, COUNT(*) FROM processed_days GROUP BY status"
        )
    )
    return {
        "processed_days": sum(counts.values()),
        "processed_day_statuses": counts,
        "daily_artifacts": connection.execute(
            "SELECT COUNT(*) FROM daily_artifacts"
        ).fetchone()[0],
        "raw_bytes": connection.execute(
            "SELECT COALESCE(SUM(bytes), 0) FROM daily_artifacts"
        ).fetchone()[0],
        "observed_m1_rows": connection.execute(
            "SELECT COUNT(*) FROM m1_bars"
        ).fetchone()[0],
        "filler_m1_rows": connection.execute(
            "SELECT COUNT(*) FROM m1_gaps"
        ).fetchone()[0],
        "zero_volume_price_change_rows": connection.execute(
            """
            SELECT COUNT(*) FROM m1_bars
            WHERE quality_status = 'OBSERVED_ZERO_VOLUME_PRICE_CHANGE'
            """
        ).fetchone()[0],
    }


def validate_database(connection, expected_keys):
    """Verify exact dataset membership, per-day counts, and raw provenance."""
    expected_keys = set(expected_keys)
    processed_keys = {
        (symbol, day)
        for symbol, day in connection.execute(
            "SELECT symbol, day_utc FROM processed_days"
        )
    }
    unexpected = sorted(processed_keys - expected_keys)
    if unexpected:
        raise RuntimeError(f"Unexpected processed TRAIN days: {unexpected[:5]}")

    approved_symbols = set(SYMBOLS)
    for table, time_column in (
        ("daily_artifacts", "day_utc"),
        ("m1_bars", "datetime"),
        ("m1_gaps", "datetime"),
    ):
        unexpected_symbols = connection.execute(
            f"SELECT DISTINCT symbol FROM {table}"
        ).fetchall()
        unexpected_symbols = sorted(
            symbol
            for (symbol,) in unexpected_symbols
            if symbol not in approved_symbols
        )
        if unexpected_symbols:
            raise RuntimeError(
                f"Unexpected symbols in {table}: {unexpected_symbols}"
            )
        if time_column == "day_utc":
            lower_bound = CONTEXT_START.strftime("%Y-%m-%d")
            upper_bound = END_EXCLUSIVE.strftime("%Y-%m-%d")
        else:
            lower_bound = CONTEXT_START.strftime(research_data.TIME_FORMAT)
            upper_bound = END_EXCLUSIVE.strftime(research_data.TIME_FORMAT)
        outside = connection.execute(
            f"""
            SELECT COUNT(*) FROM {table}
            WHERE {time_column} < ? OR {time_column} >= ?
            """,
            (lower_bound, upper_bound),
        ).fetchone()[0]
        if outside:
            raise RuntimeError(f"Rows outside locked range in {table}: {outside}")

    day_rows = connection.execute(
        """
        WITH bar_counts AS (
            SELECT symbol, substr(datetime, 1, 10) AS day_utc, COUNT(*) AS rows
            FROM m1_bars GROUP BY symbol, substr(datetime, 1, 10)
        ), gap_counts AS (
            SELECT symbol, substr(datetime, 1, 10) AS day_utc, COUNT(*) AS rows
            FROM m1_gaps GROUP BY symbol, substr(datetime, 1, 10)
        ), artifact_counts AS (
            SELECT symbol, day_utc, COUNT(*) AS rows
            FROM daily_artifacts GROUP BY symbol, day_utc
        )
        SELECT p.symbol, p.day_utc, p.bid_sha256, p.ask_sha256, p.status,
               p.source_rows, p.observed_rows, p.filler_rows,
               p.zero_volume_price_change_rows,
               COALESCE(b.rows, 0), COALESCE(g.rows, 0),
               COALESCE(a.rows, 0)
        FROM processed_days AS p
        LEFT JOIN bar_counts AS b
          ON b.symbol = p.symbol AND b.day_utc = p.day_utc
        LEFT JOIN gap_counts AS g
          ON g.symbol = p.symbol AND g.day_utc = p.day_utc
        LEFT JOIN artifact_counts AS a
          ON a.symbol = p.symbol AND a.day_utc = p.day_utc
        """
    ).fetchall()
    for row in day_rows:
        (
            symbol,
            day_text,
            bid_hash,
            ask_hash,
            status,
            source_rows,
            observed_rows,
            filler_rows,
            zero_price_rows,
            stored_bars,
            stored_gaps,
            stored_artifacts,
        ) = row
        day = datetime.strptime(day_text, "%Y-%m-%d")
        label = f"{symbol} {day_text}"
        if status == "SATURDAY_CLOSED":
            if day.weekday() != 5 or any(
                (
                    bid_hash,
                    ask_hash,
                    source_rows,
                    observed_rows,
                    filler_rows,
                    zero_price_rows,
                    stored_bars,
                    stored_gaps,
                    stored_artifacts,
                )
            ):
                raise RuntimeError(f"Invalid Saturday record: {label}")
        elif status == "MATERIALIZED":
            if day.weekday() == 5:
                raise RuntimeError(f"Saturday was materialized: {label}")
            if not bid_hash or not ask_hash or stored_artifacts != 2:
                raise RuntimeError(f"Invalid artifact provenance: {label}")
            if (
                source_rows != EXPECTED_DAILY_M1_ROWS
                or observed_rows + filler_rows != EXPECTED_DAILY_M1_ROWS
                or stored_bars != observed_rows
                or stored_gaps != filler_rows
                or zero_price_rows > observed_rows
            ):
                raise RuntimeError(f"Invalid normalized row counts: {label}")
        else:
            raise RuntimeError(f"Unexpected processed-day status: {label} {status}")

    for table in ("daily_artifacts", "m1_bars", "m1_gaps"):
        day_expression = "child.day_utc" if table == "daily_artifacts" else (
            "substr(child.datetime, 1, 10)"
        )
        orphaned = connection.execute(
            f"""
            SELECT COUNT(*) FROM {table} AS child
            WHERE NOT EXISTS (
                SELECT 1 FROM processed_days AS parent
                WHERE parent.symbol = child.symbol
                  AND parent.day_utc = {day_expression}
            )
            """
        ).fetchone()[0]
        if orphaned:
            raise RuntimeError(f"Orphaned rows in {table}: {orphaned}")

    for path_text, expected_hash, expected_bytes in connection.execute(
        "SELECT path, sha256, bytes FROM daily_artifacts ORDER BY path"
    ):
        path = Path(path_text)
        if not path.is_file() or path.stat().st_size != expected_bytes:
            raise RuntimeError(f"Missing or resized raw artifact: {path}")
        actual_hash = research_data.sha256_file(path)
        if actual_hash != expected_hash:
            raise RuntimeError(
                f"Raw artifact hash mismatch: {path} {actual_hash} != "
                f"{expected_hash}"
            )

    return sorted(expected_keys - processed_keys)


def write_checkpoint(connection, *, failures, complete=False):
    summary = progress_summary(connection)
    manifest = {
        "schema_version": 1,
        "build": "V4_DUKASCOPY_TRAIN_M1",
        "created_at_utc": datetime.now(timezone.utc).strftime(
            research_data.TIME_FORMAT
        ),
        "status": "COMPLETE" if complete else "IN_PROGRESS",
        "symbols": SYMBOLS,
        "context_start_utc": CONTEXT_START,
        "train_start_utc": TRAIN_START,
        "end_exclusive_utc": END_EXCLUSIVE,
        "validation_2025_locked": True,
        "adapter_gate_commit": ADAPTER_GATE_COMMIT,
        "adapter_gate_manifest_sha256": ADAPTER_GATE_MANIFEST_SHA256,
        "database_path": str(DATABASE_PATH),
        "raw_directory": str(RAW_DIR),
        "summary": summary,
        "failures": failures,
        "production_database_opened": False,
        "production_database_changed": False,
    }
    if complete:
        connection.commit()
        checkpoint = connection.execute(
            "PRAGMA wal_checkpoint(TRUNCATE)"
        ).fetchone()
        if checkpoint[0] != 0:
            raise RuntimeError(f"SQLite WAL checkpoint failed: {checkpoint}")
        manifest["database_sha256"] = research_data.sha256_file(DATABASE_PATH)
    digest = research_data.write_json_artifact(MANIFEST_PATH, manifest)
    return summary, digest


def run_builder():
    days = research_days()
    tasks = [(symbol, day) for symbol in SYMBOLS for day in days]
    expected_keys = {
        (symbol, day.strftime("%Y-%m-%d"))
        for symbol, day in tasks
    }
    expected_days = len(tasks)
    network_candidates = sum(
        2 for _, day in tasks if day.weekday() != 5
    )
    connection = open_database()
    failures = []
    last_network_request = None

    print("=" * 118)
    print("V4 DUKASCOPY TRAIN BUILDER | EUR/USD + GBP/USD | M1 BID/ASK")
    print("=" * 118)
    print(
        f"CONTEXT_START={CONTEXT_START} | TRAIN={TRAIN_START.year}-"
        f"{TRAIN_END.year - 1} | END_EXCLUSIVE={END_EXCLUSIVE}"
    )
    print("VALIDATION_2025_LOCKED=True")
    print(f"SYMBOL_DAYS={expected_days} | MAX_SIDE_REQUESTS={network_candidates}")
    print(f"DATABASE={DATABASE_PATH}")

    try:
        for position, (symbol, day_start) in enumerate(tasks, start=1):
            if day_start.weekday() == 5:
                status = record_saturday(connection, symbol, day_start)
                print(
                    f"DAY {position:04d}/{expected_days:04d} | {symbol} | "
                    f"{day_start:%Y-%m-%d} | {status}",
                    flush=True,
                )
                continue

            payloads = {}
            metadata = {}
            transient_failure = False
            for side in research_data.OFFER_SIDES:
                path = raw_path(symbol, day_start, side)
                seed = adapter_seed_path(symbol, day_start, side)
                has_raw_cache = path.is_file() and path.stat().st_size > 0
                needs_network = not has_raw_cache and not (
                    seed.is_file() and seed.stat().st_size > 0
                )
                if needs_network and last_network_request is not None:
                    elapsed = time.monotonic() - last_network_request
                    wait_seconds = max(
                        0.0,
                        REQUEST_INTERVAL_SECONDS - elapsed,
                    )
                    if wait_seconds:
                        time.sleep(wait_seconds)
                if needs_network:
                    last_network_request = time.monotonic()
                try:
                    payload, url, source = fetch_or_read_side(
                        symbol,
                        day_start,
                        side,
                    )
                except research_data.TransientDukascopyDownloadError as error:
                    failures.append(
                        {
                            "type": "TRANSIENT_DOWNLOAD",
                            "symbol": symbol,
                            "day_utc": day_start,
                            "side": side,
                            "error": str(error),
                        }
                    )
                    transient_failure = True
                    print(
                        f"DEFERRED | {symbol} | {day_start:%Y-%m-%d} | "
                        f"{side.upper()} | {error}",
                        flush=True,
                    )
                    time.sleep(EXHAUSTED_REQUEST_COOLDOWN_SECONDS)
                    continue
                payloads[side] = payload
                metadata[side] = (url, source)

            if transient_failure or set(payloads) != set(research_data.OFFER_SIDES):
                continue
            try:
                result = materialize_day(
                    connection,
                    symbol=symbol,
                    day_start=day_start,
                    bid_payload=payloads["bid"],
                    ask_payload=payloads["ask"],
                    bid_url=metadata["bid"][0],
                    ask_url=metadata["ask"][0],
                    bid_source=metadata["bid"][1],
                    ask_source=metadata["ask"][1],
                )
            except RequiredSourceGapError as error:
                failures.append(
                    {
                        "type": "REQUIRED_SOURCE_GAP",
                        "symbol": symbol,
                        "day_utc": day_start,
                        "error": str(error),
                    }
                )
                print(f"SOURCE_GAP | {error}", flush=True)
                continue

            print(
                f"DAY {position:04d}/{expected_days:04d} | {symbol} | "
                f"{day_start:%Y-%m-%d} | {result['status']} | "
                f"Observed={result['observed_rows']} | "
                f"Fillers={result['filler_rows']}",
                flush=True,
            )
            if position % CHECKPOINT_EVERY_DAYS == 0:
                summary, digest = write_checkpoint(
                    connection,
                    failures=failures,
                )
                print(
                    f"CHECKPOINT | Processed={summary['processed_days']}/"
                    f"{expected_days} | M1={summary['observed_m1_rows']} | "
                    f"ManifestSHA={digest}",
                    flush=True,
                )

        locked_rows = connection.execute(
            "SELECT COUNT(*) FROM m1_bars WHERE datetime >= '2025-01-01 00:00:00'"
        ).fetchone()[0]
        if locked_rows:
            raise RuntimeError(f"Locked 2025 rows materialized: {locked_rows}")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"Research database integrity failure: {integrity}")
        missing_keys = validate_database(connection, expected_keys)
        summary = progress_summary(connection)
        complete = not missing_keys and not failures
    finally:
        connection.commit()
        connection.close()

    connection = open_database()
    try:
        summary, manifest_hash = write_checkpoint(
            connection,
            failures=failures,
            complete=complete,
        )
    finally:
        connection.close()

    print()
    print("TRAIN BUILD SUMMARY")
    print("=" * 118)
    print(f"PROCESSED_DAYS={summary['processed_days']}/{expected_days}")
    print(f"OBSERVED_M1_ROWS={summary['observed_m1_rows']}")
    print(f"FILLER_M1_ROWS={summary['filler_m1_rows']}")
    print(f"RAW_BYTES={summary['raw_bytes']}")
    print(f"FAILURES={len(failures)}")
    print(f"MANIFEST={MANIFEST_PATH}")
    print(f"MANIFEST_SHA256={manifest_hash}")
    print("VALIDATION_2025_LOCKED=True")
    print("PRODUCTION_DATABASE_OPENED=False")
    print("PRODUCTION_DATABASE_CHANGED=False")
    if not complete:
        raise RuntimeError(
            "TRAIN build is incomplete; rerun the same command to reuse the "
            "verified cache and fetch only missing artifacts"
        )
    print("V4_DUKASCOPY_TRAIN_BUILD_OK")


def main():
    lock = acquire_run_lock()
    try:
        run_builder()
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


if __name__ == "__main__":
    main()
