"""
V7 robustness for the crypto DONCHIAN(20,10) long/flat candidate (chosen on TRAIN).
Info-only checks, nothing here is used to re-select parameters:
  - subsets (BTC only, BTC+ETH, without DOGE) to probe survivorship bias
  - each coin separately
  - TEST year by year
  - neighbouring parameters (is the result a knife edge?)
Usage: python3 v7_robustness.py /path/to/coinmetrics/csv
"""
import sys
import pandas as pd
import v7_crypto_gold_research as v

cfg = v.MARKETS["CRYPTO"]; per = cfg["periods"]
df = v.load_crypto(sys.argv[1])
cut = lambda r, k: r.loc[per[k][0]:per[k][1]]


def line(name, sub, prm=(20, 10)):
    r, _ = v.portfolio(sub, "DONCHIAN", prm, cfg["cost"])
    bh = v.buy_hold(sub)
    out = []
    for k in ("VALIDATION", "TEST"):
        m, b = v.metrics(cut(r, k), cfg["ann"]), v.metrics(cut(bh, k), cfg["ann"])
        out.append(f"{k[:4]} strat {100*m['total']:+6.0f}% Sh {m['sharpe']:+.2f} DD {100*m['maxdd']:4.0f}% | "
                   f"hold {100*b['total']:+6.0f}% Sh {b['sharpe']:+.2f} DD {100*b['maxdd']:4.0f}%")
    print(f"{name:12s} " + "  ||  ".join(out))
    return r, bh


print("== subsets / single coins, DONCHIAN(20,10)")
line("ALL7", df)
line("BTC", df[["BTC"]])
line("BTC+ETH", df[["BTC", "ETH"]])
line("no DOGE", df.drop(columns=["DOGE"]))
for c in df.columns:
    if c != "BTC":
        line(c, df[[c]])

print("\n== TEST by year (ALL7)")
r, bh = v.portfolio(df, "DONCHIAN", (20, 10), cfg["cost"])[0], v.buy_hold(df)
t = cut(r, "TEST"); tb = cut(bh, "TEST")
for y in sorted(set(t.index.year)):
    a = (1 + t[t.index.year == y]).prod() - 1; b = (1 + tb[tb.index.year == y]).prod() - 1
    print(f"{y}: strategy {100*a:+6.1f}%   hold {100*b:+6.1f}%")

print("\n== neighbouring parameters (ALL7)")
for prm in [(15, 7), (20, 10), (25, 12), (30, 15), (40, 20)]:
    line(str(prm), df, prm)
