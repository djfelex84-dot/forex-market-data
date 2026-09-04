"""Read-only parameter sweep over MAX_TRADE_MINUTES x TAKE_PROFIT_R_MULTIPLE.

Runs entirely on the already-downloaded, hash-verified TRAIN database
(/tmp/v4_dukascopy_train/v4_train_m1.sqlite3). Never touches 2025, never
writes to any database, never calls any network API. Safe to run alongside
the live bot.
"""

import v4_train_event_research as ter

# Grid to try. Feel free to widen later, but keep it small at first so a
# single run stays fast and easy to read.
HOLD_MINUTES_OPTIONS = [180, 270, 360]
TARGET_R_OPTIONS = [1.0, 1.2, 1.5]


def main():
    connection, manifest = ter.open_complete_train_database()
    try:
        print("=" * 118)
        print("V4 PARAM SWEEP | READ-ONLY | TRAIN=2021-2024 | VALIDATION_2025_LOCKED=True")
        print("=" * 118)
        rows = []
        for hold_minutes in HOLD_MINUTES_OPTIONS:
            for target_r in TARGET_R_OPTIONS:
                ter.MAX_TRADE_MINUTES = hold_minutes
                ter.TAKE_PROFIT_R_MULTIPLE = target_r

                records, raw_events, diagnostics, m30_quality = ter.build_records(connection)
                summary = ter.result_summary(records, raw_events)

                for setup, stats in summary.items():
                    combined = stats.get("train_combined")
                    if not combined:
                        continue
                    rows.append(
                        {
                            "setup": setup,
                            "hold_minutes": hold_minutes,
                            "target_r": target_r,
                            "n": combined.get("n"),
                            "wr": combined.get("wr"),
                            "pf": combined.get("pf"),
                            "avg_r": combined.get("avg_r"),
                            "net_r": combined.get("net_r"),
                        }
                    )

        print(
            f"{'SETUP':<16} {'HOLD(min)':>9} {'TARGET_R':>9} {'N':>6} "
            f"{'WR%':>7} {'PF':>7} {'AvgR':>8} {'NetR':>9}"
        )
        print("-" * 80)
        for row in sorted(rows, key=lambda r: (r["setup"], -(r["avg_r"] or 0))):
            print(
                f"{row['setup']:<16} {row['hold_minutes']:>9} {row['target_r']:>9.2f} "
                f"{row['n']:>6} {row['wr']:>6.2f}% {row['pf']:>7.3f} "
                f"{row['avg_r']:>+8.3f} {row['net_r']:>+9.2f}"
            )

        print()
        print("V4_PARAM_SWEEP_OK")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
