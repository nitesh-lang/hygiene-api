#!/usr/bin/env python3
# hygiene_api.py
"""
Hygiene Validator API
=====================
A small HTTP bridge between the React validator and the shared database
(hygiene_db). The React app can't call Python directly, so it talks to this
service over HTTP. Reads product data, serves the validation queue, and saves
"Mark Done" so every user's work is shared and never duplicated.

Run locally:
    pip install fastapi uvicorn psycopg2-binary
    set DATABASE_URL=postgres://...        # your Render Postgres string
    uvicorn hygiene_api:app --reload --port 8000

Deploy on Render:
    - Build command:  pip install -r requirements.txt
    - Start command:  uvicorn hygiene_api:app --host 0.0.0.0 --port $PORT
    - Env var:        DATABASE_URL = <your Render Postgres internal/external URL>

Endpoints (all JSON):
    GET  /health                      -> { ok, backend }
    GET  /products?brand=&active_only=&hide_done=
                                      -> list of products (validator's worklist)
    GET  /products/{asin}             -> one product + its validation status
    GET  /done                        -> [asins already validated]
    GET  /progress?brand=             -> { total, done, remaining, by_validator }
    POST /validate                    -> save a "Mark Done"
         body: { asin, validated_by, check_results, notes, brand }
    GET  /validation/{asin}           -> the saved validation record (or null)
"""

import os
from typing import Optional, Any, Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import hygiene_db as db

app = FastAPI(title="Hygiene Validator API", version="1.0")

# Allow the React app (any origin during setup; lock down later if you want).
# To restrict: set ALLOWED_ORIGINS="https://your-validator.onrender.com,https://..."
_origins_env = os.environ.get("ALLOWED_ORIGINS", "*")
_origins = ["*"] if _origins_env.strip() == "*" else [
    o.strip() for o in _origins_env.split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    # Make sure the tables exist on boot (products + validations).
    try:
        db.init_db()
        db.init_validations()
        db.init_input_sheet()
    except Exception as e:
        # don't crash the service; /health will report the backend
        print("startup init warning:", e)


# ---------------------------------------------------------------- models
class ValidatePayload(BaseModel):
    asin: str
    validated_by: str
    check_results: Optional[Dict[str, Any]] = None
    notes: Optional[str] = ""
    brand: Optional[str] = ""


# ---------------------------------------------------------------- routes
@app.get("/health")
def health():
    return {"ok": True, "backend": db.backend_name()}


@app.get("/products")
def products(brand: Optional[str] = None,
             active_only: bool = False,
             hide_done: bool = False) -> List[Dict[str, Any]]:
    """The validator's worklist. hide_done=True drops already-validated ASINs so
    no one re-does them."""
    rows = db.fetch_latest(brand=brand, active_only=active_only)
    if hide_done:
        done = db.list_done_asins()
        rows = [r for r in rows if str(r.get("ASIN", "")).strip() not in done]
    # annotate each row with its done status + who, so the UI can grey it out
    done_set = db.list_done_asins()
    for r in rows:
        asin = str(r.get("ASIN", "")).strip()
        r["_is_done"] = asin in done_set
    return rows


@app.get("/products/{asin}")
def product(asin: str):
    rows = db.fetch_latest()
    match = next((r for r in rows if str(r.get("ASIN", "")).strip() == asin.strip()), None)
    if not match:
        raise HTTPException(status_code=404, detail=f"ASIN {asin} not found")
    match["_validation"] = db.get_validation(asin)
    return match


@app.get("/done")
def done():
    return sorted(db.list_done_asins())


@app.get("/progress")
def progress(brand: Optional[str] = None):
    return db.validation_progress(brand=brand)


@app.post("/validate")
def validate(payload: ValidatePayload):
    """Save a 'Mark Done'. After this the ASIN is done for everyone."""
    if not payload.asin or not payload.validated_by:
        raise HTTPException(status_code=400,
                            detail="asin and validated_by are required")
    try:
        db.mark_done(
            asin=payload.asin,
            validated_by=payload.validated_by,
            check_results=payload.check_results or {},
            notes=payload.notes or "",
            brand=payload.brand or "",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "asin": payload.asin,
            "validated_by": payload.validated_by,
            "is_done": True}


@app.get("/input")
def input_sheet():
    """The current validator input/reference sheet (loaded from backend so users
    don't upload the xlsx). Returns {sheet_name, columns, rows} or null."""
    return db.get_input_sheet()


@app.get("/validations")
def validations_all(brand: Optional[str] = None):
    """Every full validation record (all users) so the app can show/export the
    whole team's work, not just the local browser's."""
    return db.list_all_validations(brand=brand)


@app.get("/validation/{asin}")
def validation(asin: str):
    return db.get_validation(asin)
