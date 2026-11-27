import numpy as np, pandas as pd

def make(path, n=30000, days=6, seed=0):
    rng = np.random.default_rng(seed)
    accts = [f"{rng.integers(0, 16**8):08X}0" for _ in range(1500)]
    banks = rng.integers(1, 40, 1500)
    cur = ["US Dollar", "Euro", "Saudi Riyal", "Yen"]
    fmts = ["ACH", "Cheque", "Credit Card", "Cash", "Wire", "Bitcoin"]
    i = rng.integers(0, 1500, n); j = rng.integers(0, 1500, n)
    t = pd.Series(pd.Timestamp("2022-09-01") + pd.to_timedelta(rng.integers(0, days * 1440, n), unit="m"))
    amt = np.round(rng.lognormal(6, 2, n), 2)
    c = rng.choice(cur, n); f = rng.choice(fmts, n)
    y = np.zeros(n, int)
    # plant some fan-out bursts through ACH so the models have something to learn
    for k in range(40):
        src = rng.integers(0, 1500); start = rng.integers(0, days * 1440 - 600)
        for m in range(8):
            idx = rng.integers(0, n)
            i[idx] = src; j[idx] = rng.integers(0, 1500)
            t[idx] = pd.Timestamp("2022-09-01") + pd.Timedelta(minutes=int(start + m * 30))
            f[idx] = "ACH"; y[idx] = 1
    df = pd.DataFrame({"Timestamp": t.dt.strftime("%Y/%m/%d %H:%M"), "From Bank": banks[i],
        "Account": [accts[x] for x in i], "To Bank": banks[j], "Account.1": [accts[x] for x in j],
        "Amount Received": amt, "Receiving Currency": c, "Amount Paid": amt, "Payment Currency": c,
        "Payment Format": f, "Is Laundering": y})
    df.to_csv(path, index=False)
