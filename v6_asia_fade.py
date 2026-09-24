"""
V6 follow-up: ASIA_FADE (derived from ASIA_BREAKOUT being negative on TRAIN).

Caveat: VALIDATION/TEST were already seen for the breakout version, so this is
a weaker test than a fully blind one. Stricter pass rule: AvgR > 0 AND t > 2
on both VALIDATION and TEST.

Rule: same trigger as ASIA_BREAKOUT (first H1 close outside the Asian range
between broker 09..16). Enter AGAINST it at that close. Target = range
midpoint. Stop = entry +/- (distance to midpoint) * K (so target = 1/K R).
Close at end of day otherwise. Variants: A) K=1.0  B) K=2.0 (chosen on TRAIN).
"""
import sys
import pandas as pd
from v6_multi_hypothesis_research import (PAIRS, PERIODS, load, r_multiple,
                                          stats, fmt)


def asia_fade(d, pair, k):
    trades = []
    for day, g in d.groupby(d.index.date):
        asia = g[g.index.hour <= 8]
        if len(asia) < 8:
            continue
        hi, lo = asia["high"].max(), asia["low"].min()
        mid = (hi + lo) / 2
        pos = None
        for t, row in g[g.index.hour >= 9].iterrows():
            if pos is None:
                if t.hour > 16:
                    break
                c = row["close"]
                if c > hi or c < lo:
                    dr = -1 if c > hi else 1
                    dist = abs(c - mid)
                    pos = (dr, c, c - dr * dist * k, mid, t)
                continue
            dr, e, s, tp, t0 = pos
            if (dr > 0 and row["low"] <= s) or (dr < 0 and row["high"] >= s):
                trades.append((t0, r_multiple(dr, e, s, s, pair), pair)); pos = None; break
            if (dr > 0 and row["high"] >= tp) or (dr < 0 and row["low"] <= tp):
                trades.append((t0, r_multiple(dr, e, tp, s, pair), pair)); pos = None; break
        if pos is not None:
            dr, e, s, tp, t0 = pos
            trades.append((t0, r_multiple(dr, e, g["close"].iloc[-1], s, pair), pair))
    return trades


root = sys.argv[1]
data = {p: load(root, p, "h1") for p in PAIRS}
res = {}
for v, k in (("A", 1.0), ("B", 2.0)):
    tr = sum((asia_fade(data[p], p, k) for p in PAIRS), [])
    res[v] = pd.DataFrame(tr, columns=["time", "R", "pair"]).dropna()
per = lambda df, x: df[(df.time >= PERIODS[x][0]) & (df.time <= PERIODS[x][1])]
for v in res:
    print(f"TRAIN var {v}: {fmt(stats(per(res[v], 'TRAIN').R))}")
ch = max(res, key=lambda v: stats(per(res[v], "TRAIN").R)["avgR"])
sv, st = stats(per(res[ch], "VALIDATION").R), stats(per(res[ch], "TEST").R)
print(f"chosen {ch}\nVALIDATION: {fmt(sv)}\nTEST      : {fmt(st)}")
ok = sv["avgR"] > 0 and sv["t"] > 2 and st["avgR"] > 0 and st["t"] > 2
print("VERDICT:", "PASS" if ok else "FAIL")
print("by pair:", res[ch].groupby("pair").R.mean().round(3).to_dict())
