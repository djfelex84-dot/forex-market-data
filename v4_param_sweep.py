"""Read-only parameter sweep over MAX_TRADE_MINUTES x TAKE_PROFIT_R_MULTIPLE."""

import v4_train_event_research as ter

HOLD_MINUTES_OPTIONS = [180, 270, 360]
TARGET_R_OPTIONS = [1.0, 1.2, 1.5]


def main():
    connection, manifest = ter.open_complete_train_database()
    try:
        print("V4 PARAM SWEEP | READ-ONLY")
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
                    rows.append({
                        "setup": setup, "hold_minutes": hold_minutes, "target_r": target_r,
                        "n": combined.get("n"), "wr": combined.get("wr"),
                        "pf": combined.get("pf"), "avg_r": combined.get("avg_r"),
                        "net_r": combined.get("net_r"),
                    })
        for row in sorted(rows, key=lambda r: (r["setup"], -(r["avg_r"] or 0))):
            print(f"{row['setup']:<16} hold={row['hold_minutes']:>4} target_r={row['target_r']:.2f} "
                  f"n={row['n']:>5} wr={row['wr']:.2f}% pf={row['pf']:.3f} "
                  f"avg_r={row['avg_r']:+.3f} net_r={row['net_r']:+.2f}")
        print("V4_PARAM_SWEEP_OK")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
