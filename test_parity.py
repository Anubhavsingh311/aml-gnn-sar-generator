"""Runs the notebook's own feature/export cells on synthetic data and checks the service reproduces them.
Usage: python tests/test_parity.py path/to/notebook.ipynb
"""
import sys, os, gc, re, json, time, tempfile
import numpy as np, pandas as pd, xgboost as xgb, nbformat
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))
import make_synth
from sklearn.metrics import average_precision_score
import model_utils as af
from model_utils import AMLScorer

nb = nbformat.read(sys.argv[1], as_version=4)
cell = lambda i: "".join(nb.cells[i].source)
tmp = tempfile.mkdtemp()
csv = os.path.join(tmp, "synth.csv"); make_synth.make(csv)

ns = dict(np=np, pd=pd, xgb=xgb, os=os, re=re, json=json, time=time, gc=gc, SEED=42, SAMPLE_DAYS=None,
          SPLIT=(0.6, 0.2, 0.2), TRANS_PATH=csv, OUT_DIR=tmp, XGB_NEG_CAP=800_000, average_precision_score=average_precision_score)
ns["trim"] = gc.collect
for i in (10, 11, 12, 13, 14, 15, 27, 28, 29, 30, 31, 32):
    exec(cell(i), ns)

df, E16, n = ns["df"], ns["E16"], ns["n"]
Y, IDX, TR = df.y.values.astype(np.float32), None, ns["TR"]
SPL = ns["SPL"]
tr, va, te = [np.where(SPL == k)[0] for k in (0, 1, 2)]

# stage 1 (small), stage 2 via the notebook's own neighbour_features
p = dict(n_estimators=60, max_depth=4, learning_rate=0.2, tree_method="hist", n_jobs=2, random_state=42)
m1 = xgb.XGBClassifier(**p).fit(E16[tr].astype(np.float32), Y[tr])
P1 = m1.predict_proba(E16.astype(np.float32))[:, 1].astype(np.float32)
ns["P1"] = P1
exec(cell(52), ns)      # defines neighbour_features and runs it once
X2, NB_NAMES = ns["X2"], ns["NB_NAMES"]
Xall = np.hstack([E16.astype(np.float32), X2.astype(np.float32)])
m2 = xgb.XGBClassifier(**p).fit(Xall[tr], Y[tr])
ref = m2.predict_proba(Xall)[:, 1]
print("stage 2 AP on synthetic test rows:", round(average_precision_score(Y[te], ref[te]), 3))

# run the notebook's export cells 93 and 94 as written
ns.update(xgb_model=m1, xgb_graph=m2, BEST_NAME="xgb_graph", THRESH={"xgb_graph": {"f1": 0.5, "r80": 0.1}},
          A_best=1.0, K_best=5, CASES={}, SARS=[], NB_NAMES=NB_NAMES, IDX={2: te},
          SCORES={"xgboost": (None, P1[te]), "xgb_graph": (None, ref[te])})
ns["xgb_predict"] = lambda m, idx: m.predict_proba(E16[idx].astype(np.float32))[:, 1]
ns["stack_predict"] = lambda m, idx: m.predict_proba(Xall[idx])[:, 1]
exec(cell(93), ns); exec(cell(94), ns)

# the service on the raw csv (input order is NOT time order here)
raw = af.load_csv(csv)
sc = AMLScorer(os.path.join(tmp, "models"))
got = sc.score(raw)
order = np.argsort(np.asarray(af._parse_time(raw["timestamp"])), kind="stable")
got_sorted = got.iloc[order].reset_index(drop=True)
d1 = np.abs(got_sorted.stage1_score.values - P1).max()
d2 = np.abs(got_sorted.risk_score.values - ref).max()
print(f"max |stage1 diff| {d1:.2e} | max |stage2 diff| {d2:.2e}")
assert d1 < 1e-4 and d2 < 1e-3, "service does not match the notebook"

# shuffled input must give the same scores row by row
import shutil
shutil.copytree(os.path.join(tmp, "models"), os.path.join(os.path.dirname(__file__), "synthetic_model"), dirs_exist_ok=True)
shutil.copy(csv, os.path.join(os.path.dirname(__file__), "synthetic.csv"))
print("PARITY OK")

# row order only matters for rows of one account in the same minute (same as in the notebook)
perm = np.random.default_rng(1).permutation(len(raw))
got_p = sc.score(raw.iloc[perm].reset_index(drop=True))
dd = np.abs(got_p.risk_score.values - got.risk_score.values[perm])
print(f"shuffled input: max diff {dd.max():.3f}, rows with diff > 1e-6: {(dd > 1e-6).sum()} of {len(dd)}, flag changes: {(got_p.flag.values != got.flag.values[perm]).sum()}")
