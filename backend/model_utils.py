"""Feature engineering and scoring for the AML service.

This is a line-for-line port of the notebook feature code (sections 5 and 8),
so that a batch of raw transactions gives the same model inputs as in training.
Standardisation constants and the high-risk cut-off come from the saved bundle.
"""
import json
import os

import numpy as np
import pandas as pd
import xgboost as xgb

T0 = pd.Timestamp("2022-09-01")
RAW_COLS = ["timestamp", "from_bank", "from_acct", "to_bank", "to_acct",
            "amt_recv", "cur_recv", "amt_paid", "cur_paid", "fmt"]
BIG = 30000.0               # "minutes since last event" when there is none
WINDOWS = (60, 1440, None)  # 1 hour, 24 hours, full history


def load_csv(src):
    """Read a CSV in the IBM HI-Small layout (header row, 10 or 11 columns)."""
    df = pd.read_csv(src, dtype=str)
    if df.shape[1] < len(RAW_COLS):
        raise ValueError(f"expected at least {len(RAW_COLS)} columns, got {df.shape[1]}")
    df = df.iloc[:, :len(RAW_COLS)]
    df.columns = RAW_COLS
    return df


def _parse_time(col):
    try:
        t = pd.to_datetime(col, format="%Y/%m/%d %H:%M")
    except (ValueError, TypeError):
        t = pd.to_datetime(col)
    return (t - T0) // pd.Timedelta(minutes=1)


def _account_key(bank, acct):
    b = pd.to_numeric(bank).astype("int64").values
    a = acct.astype(str).map(lambda x: int(x, 16)).astype("int64").values
    return (b << 36) | a


def prepare(raw):
    """Raw transactions (RAW_COLS) -> arrays. Row order is kept as given."""
    missing = [c for c in RAW_COLS if c not in raw.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    if len(raw) == 0:
        raise ValueError("no transactions supplied")
    kS = _account_key(raw["from_bank"], raw["from_acct"])
    kD = _account_key(raw["to_bank"], raw["to_acct"])
    _, inv = np.unique(np.concatenate([kS, kD]), return_inverse=True)
    n = len(raw)
    return dict(
        ts=np.asarray(_parse_time(raw["timestamp"]), dtype=np.int64),
        S=inv[:n].astype(np.int64), D=inv[n:].astype(np.int64),
        AP=pd.to_numeric(raw["amt_paid"]).astype("float64").values,
        AR=pd.to_numeric(raw["amt_recv"]).astype("float64").values,
        cur_p=raw["cur_paid"].astype(str).values, cur_r=raw["cur_recv"].astype(str).values,
        fmt=raw["fmt"].astype(str).values,
        bank_s=kS >> 36, bank_d=kD >> 36,
    )


def _sorted_events(acct, amount, ts, TT):
    key = acct * TT + ts
    order = np.argsort(key, kind="stable")
    cum = np.concatenate([[0.0], np.cumsum(amount[order])])
    return key[order], cum, ts[order], amount[order]


def raw_edge_features(t, fmt_names, cur_names):
    """Unstandardised edge features, in the same order as the notebook."""
    S, D, ts, AP, AR = t["S"], t["D"], t["ts"], t["AP"], t["AR"]
    TT = int(ts.max()) + 2
    events = {"out": _sorted_events(S, AP, ts, TT), "in": _sorted_events(D, AR, ts, TT)}

    f = {}
    sums_24h, sums_all, last_gap, last_amt, first_seen = {}, {}, {}, {}, {}
    for role, acct in (("s", S), ("d", D)):
        for kind in ("out", "in"):
            key_sorted, cum, t_sorted, a_sorted = events[kind]
            hi = np.searchsorted(key_sorted, acct * TT + ts, side="left")
            for W in WINDOWS:
                start = np.maximum(ts - W, 0) if W else 0
                lo = np.searchsorted(key_sorted, acct * TT + start, side="left")
                label = W or "all"
                f[f"{role}_{kind}_cnt_{label}"] = np.log1p(hi - lo)
                if W != 60:
                    total = cum[hi] - cum[lo]
                    f[f"{role}_{kind}_sum_{label}"] = np.log1p(total)
                    (sums_24h if W == 1440 else sums_all)[(role, kind)] = total.astype(np.float64)
                if W is None:
                    has_any = hi > lo
                    last = np.where(has_any, hi - 1, 0)
                    last_gap[(role, kind)] = np.where(has_any, ts - t_sorted[last], BIG)
                    last_amt[(role, kind)] = np.where(has_any, a_sorted[last], 0.0)
                    first_seen[(role, kind)] = np.where(has_any, t_sorted[np.where(has_any, lo, 0)], ts)

    for key, name in {("s", "in"): "s_lastin_gap", ("s", "out"): "s_lastout_gap",
                      ("d", "in"): "d_lastin_gap", ("d", "out"): "d_lastout_gap"}.items():
        f[name] = np.log1p(last_gap[key])

    had_incoming = last_gap[("s", "in")] < BIG
    ratio = np.where(had_incoming, AP / (last_amt[("s", "in")] + 1e-6), 0)
    f["s_lastin_ratio"] = np.log1p(np.clip(ratio, 0, 1e3))
    f["s_amt_vs_in24"] = np.log1p(np.clip(AP / (sums_24h[("s", "in")] + 1), 0, 1e3))
    f["s_amt_vs_inall"] = np.log1p(np.clip(AP / (sums_all[("s", "in")] + 1), 0, 1e3))
    f["s_age"] = np.log1p(ts - np.minimum(first_seen[("s", "out")], first_seen[("s", "in")]))
    f["d_age"] = np.log1p(ts - np.minimum(first_seen[("d", "out")], first_seen[("d", "in")]))

    hour = (ts % 1440) / 60.0
    f["log_amt_paid"] = np.log1p(AP)
    f["log_amt_recv"] = np.log1p(AR)
    f["cross_currency"] = t["cur_p"] != t["cur_r"]
    f["cross_bank"] = t["bank_s"] != t["bank_d"]
    f["self_transfer"] = S == D
    f["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    f["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    for name in fmt_names:
        f[f"fmt_{name}"] = t["fmt"] == name
    for name in cur_names:
        f[f"cur_{name}"] = t["cur_p"] == name
    return f


def edge_matrix(t, spec):
    """Standardised float16 edge feature matrix, columns in the training order."""
    f = raw_edge_features(t, spec["format_names"], spec["currency_names"])
    names = spec["edge_feature_names"]
    if set(f) != set(names):
        raise ValueError(f"feature mismatch: {sorted(set(f) ^ set(names))}")
    out = np.zeros((len(t["ts"]), len(names)), dtype=np.float16)
    for i, name in enumerate(names):
        a = np.asarray(f[name], dtype=np.float32)
        out[:, i] = np.clip((a - spec["edge_mu"][i]) / spec["edge_sd"][i], -10, 10).astype(np.float16)
    return out


def neighbour_matrix(t, P, high_cut):
    """Stage 2 features: stage 1 scores of the other transactions at the same sender/receiver.

    Uses transactions before AND after the current one (+/- window), so this is
    a retrospective, batch-level feature, not a real-time one.
    """
    S, D, ts = t["S"], t["D"], t["ts"]
    n = len(ts)
    TT = int(ts.max()) + 2
    P = np.asarray(P, dtype=np.float32)
    is_high = (P >= high_cut).astype(np.float32)

    keys = np.concatenate([S * TT + ts, D * TT + ts])
    order = np.argsort(keys, kind="stable")
    keys_sorted = keys[order]

    both = np.concatenate([P, P]).astype(np.float64)
    cum_p = np.zeros(2 * n + 1)
    np.cumsum(both[order], out=cum_p[1:])
    both32 = np.concatenate([is_high, is_high])
    cum_h = np.zeros(2 * n + 1, dtype=np.float32)
    np.cumsum(both32[order], out=cum_h[1:])

    self_mult = 1 + (S == D).astype(np.int64)
    cols, names = [], []
    for role, acct in (("s", S), ("d", D)):
        for W in WINDOWS:
            start = np.maximum(ts - W, 0) if W else 0
            end = np.minimum(ts + W, TT - 1) if W else TT - 1
            lo = np.searchsorted(keys_sorted, acct * TT + start, side="left")
            hi = np.searchsorted(keys_sorted, acct * TT + end, side="right")
            count = np.maximum(hi - lo - self_mult, 0)
            score_sum = cum_p[hi] - cum_p[lo] - self_mult * P
            n_high = cum_h[hi] - cum_h[lo] - self_mult * is_high
            mean_score = np.maximum(score_sum, 0) / np.maximum(count, 1)
            cols.append(np.log10(mean_score + 1e-6).astype(np.float16))
            cols.append(np.log1p(np.maximum(n_high, 0)).astype(np.float16))
            names += [f"nb_{role}_meanp_{W or 'all'}", f"nb_{role}_highrisk_{W or 'all'}"]

    pc = np.clip(P, 1e-6, 1 - 1e-6)
    cols.append(np.log(pc / (1 - pc)).astype(np.float16))
    names.append("stage1_logit")
    return np.column_stack(cols), names


CHUNK = 250_000


class AMLScorer:
    def __init__(self, model_dir):
        with open(os.path.join(model_dir, "preprocessing_and_thresholds.json")) as f:
            self.spec = json.load(f)
        if "neighbour_high_cut" not in self.spec:
            raise RuntimeError("bundle was exported by the old notebook (no neighbour_high_cut). "
                               "Re-run the updated notebook and export again.")
        self.best = self.spec["best_model"]
        if self.best != "xgb_graph":
            raise RuntimeError(f"this service serves the xgb_graph pipeline, bundle best model is {self.best}")
        self.stage1 = xgb.XGBClassifier()
        self.stage1.load_model(os.path.join(model_dir, "xgboost_aml.json"))
        self.stage2 = xgb.XGBClassifier()
        self.stage2.load_model(os.path.join(model_dir, "xgboost_graph_stage2_aml.json"))
        thr = self.spec["thresholds"][self.best]
        self.thr_f1, self.thr_r80 = float(thr["f1"]), float(thr["r80"])

    @staticmethod
    def _predict(model, X):
        return np.concatenate([model.predict_proba(X[i:i + CHUNK].astype(np.float32))[:, 1]
                               for i in range(0, len(X), CHUNK)])

    def score(self, raw):
        """raw: DataFrame with RAW_COLS. Returns one row per input row, same order."""
        order = np.argsort(pd.to_numeric(_parse_time(raw["timestamp"])).values, kind="stable")
        srt = raw.iloc[order].reset_index(drop=True)      # the notebook works on time-sorted rows
        t = prepare(srt)

        E = edge_matrix(t, self.spec)
        p1 = self._predict(self.stage1, E)
        X2, names = neighbour_matrix(t, p1, self.spec["neighbour_high_cut"])
        if names != self.spec["neighbour_feature_names"]:
            raise RuntimeError("neighbour feature names differ from the bundle")
        p2 = self._predict(self.stage2, np.hstack([E.astype(np.float32), X2.astype(np.float32)]))

        out = pd.DataFrame({"stage1_score": p1, "risk_score": p2})
        out["flag"] = out.risk_score >= self.thr_f1
        out["flag_high_recall"] = out.risk_score >= self.thr_r80
        out.index = order                                  # undo the sort
        out = out.sort_index().reset_index(drop=True)
        return out

    def info(self):
        return dict(model=self.best, thr_f1=self.thr_f1, thr_recall80=self.thr_r80,
                    n_edge_features=len(self.spec["edge_feature_names"]),
                    n_neighbour_features=len(self.spec["neighbour_feature_names"]),
                    formats=self.spec["format_names"], currencies=self.spec["currency_names"])
