"""FastAPI backend. Local: uvicorn main:app --reload"""
import io
import os
from typing import List, Union

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import model_utils as mu

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.environ.get("AML_MODEL_DIR", os.path.join(HERE, "model"))
MAX_ROWS = int(os.environ.get("AML_MAX_ROWS", "150000"))     # free Render instances have 512 MB of RAM
ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",")]

app = FastAPI(title="AML transaction scorer", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["*"], allow_headers=["*"])
scorer = mu.AMLScorer(MODEL_DIR)

SHOW = ["timestamp", "from_bank", "from_acct", "to_bank", "to_acct", "amt_paid", "cur_paid", "fmt"]


class Txn(BaseModel):
    timestamp: str                 # "2022/09/10 14:05" or ISO
    from_bank: Union[int, str]
    from_acct: str                 # hex account id, as in the IBM data
    to_bank: Union[int, str]
    to_acct: str
    amt_recv: float
    cur_recv: str
    amt_paid: float
    cur_paid: str
    fmt: str


class ScoreRequest(BaseModel):
    transactions: List[Txn]
    only_flagged: bool = False
    limit: int = 500


def _run(raw: pd.DataFrame, only_flagged: bool, limit: int):
    if len(raw) > MAX_ROWS:
        raise HTTPException(413, f"max {MAX_ROWS} rows per request, got {len(raw)}")
    try:
        res = scorer.score(raw)
        t = mu._parse_time(raw["timestamp"])
    except (ValueError, KeyError) as e:
        raise HTTPException(422, str(e))
    span_h = float((t.max() - t.min()) / 60)
    res.insert(0, "row", range(len(res)))
    n_flag, n_flag_hr = int(res.flag.sum()), int(res.flag_high_recall.sum())
    if only_flagged:
        res = res[res.flag]
    res = res.sort_values("risk_score", ascending=False).head(max(limit, 0))
    res = res.join(raw[SHOW].reset_index(drop=True), on="row")
    return dict(n_rows=len(raw), n_flagged=n_flag, n_flagged_high_recall=n_flag_hr,
                span_hours=round(span_h, 1), returned=len(res),
                note="scores depend on the history in the batch; send several days of data per call",
                results=res.round(6).to_dict(orient="records"))


@app.get("/")
@app.get("/health")
def health():
    return dict(status="ok", **scorer.info())


@app.post("/score")
def score(req: ScoreRequest):
    raw = pd.DataFrame([t.model_dump() for t in req.transactions])
    return _run(raw, req.only_flagged, req.limit)


@app.post("/score_csv")
async def score_csv(file: UploadFile = File(...), only_flagged: bool = True, limit: int = 500):
    try:
        raw = mu.load_csv(io.BytesIO(await file.read()))
    except Exception as e:
        raise HTTPException(422, f"cannot read csv: {e}")
    return _run(raw, only_flagged, limit)
