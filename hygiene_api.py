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
    GET  /specs                       -> our own dims/weight + volumetric per ASIN
    GET  /spec-changes?brand=         -> Amazon-side dims/weight changes over time
"""

import os
from typing import Optional, Any, Dict, List

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import hygiene_db as db

app = FastAPI(title="Hygiene Validator API", version="1.0")

# ── AUTH ─────────────────────────────────────────────────────────────────────
# Every data route requires the shared key in the `x-api-key` header. Set it on
# Render:  API_KEY=<same value the frontend's VITE_API_KEY uses>.
# Fail-open ONLY while API_KEY is unset, so deploying this code BEFORE you set the
# env var can't lock anyone out — but the API is NOT protected until API_KEY is set.
API_KEY = os.environ.get("API_KEY", "").strip()
# Never require auth. /login MUST be here — it is how a caller obtains
# credentials in the first place, so gating it behind them locks everyone out.
_OPEN_PATHS = {"/", "/health", "/login"}


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    return auth[7:].strip() if auth[:7].lower() == "bearer " else ""


@app.middleware("http")
async def require_api_key(request: Request, call_next):
    if request.method == "OPTIONS":           # CORS preflight carries no auth header
        return await call_next(request)
    if request.url.path in _OPEN_PATHS:       # health checks must stay open for Render
        return await call_next(request)
    # A signed-in user's bearer token is accepted anywhere the shared key is.
    # Both are allowed during the migration off the in-bundle key so the browser
    # can switch to tokens without a flag-day that locks the team out; once no
    # client sends x-api-key, unset API_KEY and only tokens will work.
    #
    # Deny by default. This used to fall through to call_next() whenever API_KEY
    # was empty, so an unset or mistyped env var silently published the entire
    # database to anyone who knew the URL. Now a caller must present something
    # valid, and clearing API_KEY tightens the API instead of opening it.
    token = _bearer(request)
    if token and db.session_user(token):
        return await call_next(request)
    if API_KEY and request.headers.get("x-api-key") == API_KEY:
        return await call_next(request)
    return JSONResponse({"error": "unauthorized"}, status_code=401)


# ── CORS ─────────────────────────────────────────────────────────────────────
# Lock to your frontend origin:  ALLOWED_ORIGINS="https://your-validator.onrender.com"
# allow_credentials is now False (we authenticate with a header key, not cookies),
# which also closes the previous reflect-ANY-origin-with-credentials hole.
# Added AFTER the auth middleware so CORS is the OUTERMOST layer: preflight is
# answered correctly and even a 401 response still carries CORS headers.
_origins_env = os.environ.get("ALLOWED_ORIGINS", "*")
_origins = ["*"] if _origins_env.strip() == "*" else [
    o.strip() for o in _origins_env.split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    # Authorization MUST be here: every signed-in request carries the bearer
    # token in it, and a header missing from this list makes the browser fail
    # the preflight before the request is ever sent — which surfaces as
    # "can't reach the server", not as a 401.
    allow_headers=["Content-Type", "x-api-key", "Authorization"],
)


@app.on_event("startup")
def _startup():
    # Make sure the tables exist on boot (products + validations).
    try:
        db.init_db()
        db.init_validations()
        db.init_input_sheet()
        db.init_users()
        db.init_product_specs()
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
    """Reports whether the DATABASE actually answers, not just which driver is
    configured. The old version returned ok:true off the URL prefix alone, so
    Render showed this service green through an outage in which every data
    route was 500ing."""
    try:
        db.list_done_asins()
        return {"ok": True, "backend": db.backend_name(), "db": "up"}
    except Exception as e:
        return JSONResponse(
            {"ok": False, "backend": db.backend_name(), "db": "down",
             "error": type(e).__name__},
            status_code=503)


# ── login ────────────────────────────────────────────────────────────────────
class LoginPayload(BaseModel):
    # Sign in with the work email; the display name is accepted too so an
    # account still works before its address has been filled in.
    name: str
    password: str


class ChangePasswordPayload(BaseModel):
    current_password: str
    new_password: str


@app.post("/login")
def login(payload: LoginPayload):
    """Exchange name + password for a bearer token. The team's credentials used
    to sit in plaintext inside the browser bundle; they're hashed in the DB now
    and the browser only ever holds an opaque token.

    must_change=True means the password was issued by someone else and the user
    has to replace it before working — the client gates the app on this."""
    user = db.verify_user(payload.name, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect name or password.")
    return {"token": db.create_session(user["name"]),
            "name": user["name"], "email": user.get("email", ""),
            "admin": user["is_admin"],
            "must_change": user["must_change"]}


@app.post("/change-password")
def change_password(payload: ChangePasswordPayload, request: Request):
    """Set your own password. Requires the current one, so a stolen token can't
    be used to take over the account. Every session is dropped afterwards,
    including this one, so the client logs back in with the new password."""
    who = db.session_user(_bearer(request))
    if not who:
        raise HTTPException(status_code=401, detail="Sign in first.")
    try:
        db.set_own_password(who["name"], payload.current_password, payload.new_password)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "name": who["name"]}


@app.post("/logout")
def logout(request: Request):
    token = _bearer(request)
    if token:
        db.delete_session(token)
    return {"ok": True}


@app.get("/me")
def me(request: Request):
    """Who the caller is. Returns null for a shared-key caller with no token,
    which is how the client tells 'signed in' from 'using the legacy key'."""
    return db.session_user(_bearer(request))


@app.get("/users")
def users(request: Request):
    """Admin-only: the roster, never any hashes."""
    who = db.session_user(_bearer(request))
    if not who or not who.get("is_admin"):
        raise HTTPException(status_code=403, detail="admin only")
    return db.list_users()


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
    # Fill the brand from the crawl when the client can't supply it (the app's
    # offline-catch-up push runs before the product list has loaded). Without
    # this the row stores brand='' and /progress?brand=... — which matches on
    # LOWER(brand) LIKE — silently under-counts it.
    brand = (payload.brand or "").strip()
    if not brand:
        asin = payload.asin.strip()
        match = next((r for r in db.fetch_latest()
                      if str(r.get("ASIN", "")).strip() == asin), None)
        if match:
            brand = str(match.get("Brand", "") or "").strip()
    try:
        db.mark_done(
            asin=payload.asin,
            validated_by=payload.validated_by,
            check_results=payload.check_results or {},
            notes=payload.notes or "",
            brand=brand,
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


@app.get("/specs")
def specs():
    """OUR OWN measured dimensions (cm) and packed weight (kg), per ASIN.

    Separate from /input on purpose. The input sheet's weight and dimension
    columns were filled in from Amazon's own PDP, so they cannot be used to
    check Amazon against — this is the independent record. Returns [] until a
    sheet has been imported with `python hygiene_db.py import-specs <file>`,
    and the UI simply shows nothing rather than failing.
    """
    rows = db.get_product_specs()
    for r in rows:
        vol = db.volumetric_kg(r.get("length_cm"), r.get("breadth_cm"),
                               r.get("height_cm"))
        r["volumetric_kg"] = vol
        r["chargeable_kg"] = db.chargeable_kg(r.get("weight_kg"), vol)
    return rows


@app.get("/spec-changes")
def spec_changes(brand: Optional[str] = None):
    """What Amazon changed on its side: previous vs current crawled Dimensions
    and Weight per ASIN, with the crawl timestamps. Empty until a second crawl
    run exists — the history it reads from is built one crawl at a time."""
    return db.spec_changes(brand=brand)


@app.get("/validations")
def validations_all(brand: Optional[str] = None):
    """Every full validation record (all users) so the app can show/export the
    whole team's work, not just the local browser's."""
    return db.list_all_validations(brand=brand)


@app.get("/validation/{asin}")
def validation(asin: str):
    return db.get_validation(asin)
