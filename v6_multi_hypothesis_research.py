"""
V6 research: three pre-registered hypotheses on H1/H4, 11 FX pairs.

Data: github.com/ejtraderLabs/historical-data (broker time, week opens
Monday 00:00 = UTC+2/+3). 2012-11 .. 2022-03.

Locked split (fixed before any result was seen):
  TRAIN 2013-2017 (variant choice only here)
  VALIDATION 2018-2019
  TEST 2020-01 .. 2022-03
Pass rule: chosen variant must have AvgR > 0 and PF > 1.10 on VALIDATION
AND AvgR > 0 on TEST.

Hypotheses (each has 2 variants, chosen on TRAIN):
  ASIA_BREAKOUT  H1. Asian range = broker hours 00..08. From 09 to 16, first
                 close outside the range -> enter at that close, stop at the
                 opposite side of the range. Exit: A) target 1R, B) no target,
                 close at broker 23:00.
  DONCHIAN_H4    H4 trend. Close above N-bar high -> long (mirror short),
                 stop 2 ATR20, exit on close through M-bar opposite channel.
                 A) N=55,M=20  B) N=20,M=10.
  CROSS_MR       H1 mean reversion on the
                 low-trend crosses EURGBP and EURCHF only. z = (close-SMA100)
                 / std100. |z| > Z -> fade at close, exit when z crosses 0 or
                 after 72 bars, stop = entry +/- 3 ATR14. A) Z=2  B) Z=3.
Costs: round-trip spread+slippage in basis points per pair (below).
Results are in R (risk = entry-to-stop distance), cost included.

Usage: python3 v6_multi_hypothesis_research.py /path/to/historical-data
"""

import os
import sys

import numpy as np
import pandas as pd

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF",
         "EURGBP", "EURJPY", "EURCHF", "GBPJPY", "AUDJPY"]
COST_BP = {"EURUSD": 1.5, "USDJPY": 1.5, "GBPUSD": 2.0, "AUDUSD": 2.0,
           "USDCAD": 2.0, "USDCHF": 2.0, "EURGBP": 2.0, "EURJPY": 2.5,
           "EURCHF": 2.5, "GBPJPY": 3.5, "AUDJPY": 3.5}
PERIODS = {"TRAIN": ("2013-01-01", "2017-12-31"),
           "VALIDATION": ("2018-01-01", "2019-12-31"),
           "TEST": ("2020-01-01", "2022-12-31")}


def load(root, pair, tf):
    d = pd.read_csv(os.path.join(root, pair, f"{pair}{tf}.csv"),
                    parse_dates=["Date"]).set_index("Date").sort_index()
    return d[["open", "high", "low", "close"]].astype(float)


def atr(d, n):
    pc = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(),
                    (d["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def r_multiple(direction, entry, exit_px, stop, pair):
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    cost = entry * COST_BP[pair] / 1e4
    return (direction * (exit_px - entry) - cost) / risk


def asia_breakout(d, pair, variant):
    trades = []
    for day, g in d.groupby(d.index.date):
        asia = g[g.index.hour <= 8]
        if len(asia) < 8:
            continue
        hi, lo = asia["high"].max(), asia["low"].min()
        rest = g[g.index.hour >= 9]
        pos = None
        for t, row in rest.iterrows():
            if pos is None:
                if t.hour > 16:
                    break
                if row["close"] > hi:
                    pos = (1, row["close"], lo, t)
                elif row["close"] < lo:
                    pos = (-1, row["close"], hi, t)
                if pos and variant == "A":
                    dr, e, s, _ = pos
                    pos = pos + (e + dr * abs(e - s),)
                continue
            dr, e, s, t0 = pos[:4]
            if (dr > 0 and row["low"] <= s) or (dr < 0 and row["high"] >= s):
                trades.append((t0, r_multiple(dr, e, s, s, pair)))
                pos = None
                break
            if variant == "A":
                tp = pos[4]
                if (dr > 0 and row["high"] >= tp) or (dr < 0 and row["low"] <= tp):
                    trades.append((t0, r_multiple(dr, e, tp, s, pair)))
                    pos = None
                    break
        if pos is not None:
            dr, e, s, t0 = pos[:4]
            trades.append((t0, r_multiple(dr, e, g["close"].iloc[-1], s, pair)))
    return trades


def donchian(d, pair, variant):
    n, m = (55, 20) if variant == "A" else (20, 10)
    a = atr(d, 20).values
    up = d["high"].rolling(n).max().shift(1).values
    dn = d["low"].rolling(n).min().shift(1).values
    xu = d["high"].rolling(m).max().shift(1).values
    xd = d["low"].rolling(m).min().shift(1).values
    c, h, l = d["close"].values, d["high"].values, d["low"].values
    idx = d.index
    trades, pos = [], None
    for i in range(n + 1, len(d)):
        if pos:
            dr, e, s, t0 = pos
            if (dr > 0 and l[i] <= s) or (dr < 0 and h[i] >= s):
                trades.append((t0, r_multiple(dr, e, s, s, pair)))
                pos = None
            elif (dr > 0 and c[i] < xd[i]) or (dr < 0 and c[i] > xu[i]):
                trades.append((t0, r_multiple(dr, e, c[i], s, pair)))
                pos = None
            continue
        if c[i] > up[i]:
            pos = (1, c[i], c[i] - 2 * a[i], idx[i])
        elif c[i] < dn[i]:
            pos = (-1, c[i], c[i] + 2 * a[i], idx[i])
    return trades


def cross_mr(d, pair, variant):
    z_in = 2.0 if variant == "A" else 3.0
    sma = d["close"].rolling(100).mean()
    z = ((d["close"] - sma) / d["close"].rolling(100).std()).values
    a = atr(d, 14).values
    c, h, l = d["close"].values, d["high"].values, d["low"].values
    idx = d.index
    trades, pos = [], None
    for i in range(101, len(d)):
        if pos:
            dr, e, s, t0, i0 = pos
            if (dr > 0 and l[i] <= s) or (dr < 0 and h[i] >= s):
                trades.append((t0, r_multiple(dr, e, s, s, pair)))
                pos = None
            elif (dr > 0 and z[i] >= 0) or (dr < 0 and z[i] <= 0) or i - i0 >= 72:
                trades.append((t0, r_multiple(dr, e, c[i], s, pair)))
                pos = None
            continue
        if z[i] > z_in:
            pos = (-1, c[i], c[i] + 3 * a[i], idx[i], i)
        elif z[i] < -z_in:
            pos = (1, c[i], c[i] - 3 * a[i], idx[i], i)
    return trades


HYPOTHESES = {
    "ASIA_BREAKOUT": (asia_breakout, "h1", PAIRS),
    "DONCHIAN_H4": (donchian, "h4", PAIRS),
    "CROSS_MR": (cross_mr, "h1", ["EURGBP", "EURCHF"]),
}


def stats(r):
    r = np.asarray([x for x in r if x is not None and np.isfinite(x)])
    if len(r) == 0:
        return dict(n=0, win=0, avgR=0, PF=0, t=0)
    w, lo = r[r > 0].sum(), -r[r <= 0].sum()
    return dict(n=len(r), win=100 * (r > 0).mean(), avgR=r.mean(),
                PF=w / lo if lo > 0 else np.inf,
                t=r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 1 else 0)


def fmt(s):
    return (f"n={s['n']:5d} win={s['win']:5.1f}% avgR={s['avgR']:+.3f} "
            f"PF={s['PF']:.2f} t={s['t']:+.2f}")


def main():
    root = sys.argv[1]
    data = {}
    for name, (fn, tf, pairs) in HYPOTHESES.items():
        print(f"\n######## {name}")
        res = {}
        for v in ("A", "B"):
            tr = []
            for p in pairs:
                key = (p, tf)
                if key not in data:
                    data[key] = load(root, p, tf)
                tr += [(t, r, p) for t, r in fn(data[key], p, v)]
            res[v] = pd.DataFrame(tr, columns=["time", "R", "pair"]).dropna()
        per = lambda df, k: df[(df.time >= PERIODS[k][0]) & (df.time <= PERIODS[k][1])]
        for v in ("A", "B"):
            print(f"  TRAIN var {v}: {fmt(stats(per(res[v], 'TRAIN').R))}")
        chosen = max(("A", "B"), key=lambda v: stats(per(res[v], "TRAIN").R)["avgR"])
        print(f"  -> chosen variant {chosen} (by TRAIN AvgR)")
        sv = stats(per(res[chosen], "VALIDATION").R)
        st = stats(per(res[chosen], "TEST").R)
        print(f"  VALIDATION: {fmt(sv)}")
        print(f"  TEST      : {fmt(st)}")
        ok = sv["avgR"] > 0 and sv["PF"] > 1.10 and st["avgR"] > 0
        print(f"  VERDICT: {'PASS' if ok else 'FAIL'}")
        df = res[chosen]
        by_pair = df.groupby("pair").R.mean().round(3).to_dict()
        print(f"  AvgR by pair (all years, info): {by_pair}")


if __name__ == "__main__":
    main()
