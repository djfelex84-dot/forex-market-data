"""Read-only check: does cross-pair directional confirmation improve either V4 pattern's price-path anatomy?"""

from datetime import timedelta

import v4_event_anatomy as anatomy
import v4_research_data as research_data
import v4_train_event_research as ter
from v4_event_strategy import (
    SETUP_BREAKOUT_RETEST,
    SETUP_FAKEOUT,
    TRAIN_END,
    TRAIN_START,
    generate_v4_events,
)

HORIZONS = anatomy.HORIZONS_MINUTES
MAX_HORIZON = max(HORIZONS)
SETUPS = (SETUP_FAKEOUT, SETUP_BREAKOUT_RETEST)
SYMBOL_PAIRS = {"EUR/USD": "GBP/USD", "GBP/USD": "EUR/USD"}

BUCKET_ALIGNED = "ALIGNED"
BUCKET_NOT_ALIGNED = "NOT_ALIGNED"


def build_other_symbol_return_index(strategy_rows):
    index = {}
    for row in strategy_rows:
        close_time = row["_time"] + timedelta(minutes=30)
        change = float(row["close"]) - float(row["open"])
        index[close_time] = 1 if change > 0 else (-1 if change < 0 else 0)
    return index


def classify_alignment(direction, other_return_sign):
    if other_return_sign == 0:
        return None
    if direction == "BUY":
        return BUCKET_ALIGNED if other_return_sign > 0 else BUCKET_NOT_ALIGNED
    return BUCKET_ALIGNED if other_return_sign < 0 else BUCKET_NOT_ALIGNED


def build_records_with_alignment(connection, symbol, other_return_index):
    m30_rows, _quality = ter.load_verified_m30(connection, symbol)
    strategy_rows = research_data.m30_strategy_rows(m30_rows, side="mid")
    scan = generate_v4_events(strategy_rows)

    records = []
    skipped_missing_other = 0
    for setup in SETUPS:
        for event in scan[setup]:
            signal_time = research_data.parse_utc(event["signal_time"])
            if not TRAIN_START <= signal_time < TRAIN_END:
                continue

            other_sign = other_return_index.get(signal_time)
            if other_sign is None:
                skipped_missing_other += 1
                continue
            bucket = classify_alignment(event["direction"], other_sign)
            if bucket is None:
                continue

            window_end = signal_time + timedelta(minutes=MAX_HORIZON)
            if window_end > TRAIN_END:
                m1_index = {}
            else:
                window_rows = ter.iter_source_grid(
                    connection, symbol, signal_time, window_end
                )
                m1_index = anatomy.index_m1(list(window_rows))

            record = anatomy.analyze_event(
                symbol=symbol,
                event=event,
                m1_index=m1_index,
                split_end=TRAIN_END,
                horizons=HORIZONS,
            )
            record["alignment_bucket"] = bucket
            records.append(record)
    return records, skipped_missing_other


def report_bucket(records, setup, bucket, horizon):
    subset = [
        row
        for row in records
        if row["setup"] == setup and row["alignment_bucket"] == bucket
    ]
    summary = anatomy.summarize(subset, horizon=horizon)
    if summary["evaluated"] == 0:
        print(f"{setup:<16} {bucket:<12} HORIZON={horizon:>3}min | no evaluated events")
        return
    field = f"fr_{horizon}m_atr"
    try:
        boot = anatomy.day_block_bootstrap_mean(subset, field=field)
        lo95, hi95 = boot["ci_95"]
        flag = "NO SIGNAL" if lo95 <= 0 <= hi95 else "SIGNAL"
        ci_text = f"CI95=({lo95:+.4f},{hi95:+.4f}) {flag}"
    except ValueError as error:
        ci_text = str(error)
    print(
        f"{setup:<16} {bucket:<12} HORIZON={horizon:>3}min | "
        f"n={summary['evaluated']:>4} mean_fr_atr={summary['mean_fr_atr']:>+7.4f} | {ci_text}"
    )


def main():
    connection, _manifest = ter.open_complete_train_database()
    try:
        return_indexes = {}
        for symbol in ter.SYMBOLS:
            m30_rows, _quality = ter.load_verified_m30(connection, symbol)
            strategy_rows = research_data.m30_strategy_rows(m30_rows, side="mid")
            return_indexes[symbol] = build_other_symbol_return_index(strategy_rows)

        all_records = []
        total_skipped = 0
        for symbol in ter.SYMBOLS:
            other_symbol = SYMBOL_PAIRS[symbol]
            records, skipped = build_records_with_alignment(
                connection, symbol, return_indexes[other_symbol]
            )
            all_records.extend(records)
            total_skipped += skipped

        print("=" * 110)
        print("V4 EVENT ANATOMY BY CROSS-PAIR ALIGNMENT | TRAIN ONLY")
        print(f"(skipped {total_skipped} events with no matching other-symbol bar)")
        print("=" * 110)

        for setup in SETUPS:
            for bucket in (BUCKET_ALIGNED, BUCKET_NOT_ALIGNED):
                for horizon in HORIZONS:
                    report_bucket(all_records, setup, bucket, horizon)
            print("-" * 110)

        print("V4_EVENT_ANATOMY_BY_ALIGNMENT_OK")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
