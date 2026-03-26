import logging
import os
import threading
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from submit_case_workflow import submit_case_workflow
from utils_paths import load_json, normalize_case_id, stub_path
from utils_rate_limit import _rate_check
from utils_security import get_client_ip, receipt_matches
from utils_stub import public_stub_view

logger = logging.getLogger("external")
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
RESULT_RATE_LIMIT = int(os.getenv("RESULT_RATE_LIMIT", "120"))
RESULT_RATE_WINDOW_SEC = int(os.getenv("RESULT_RATE_WINDOW_SEC", "600"))
LIFF_ID = os.getenv("LIFF_ID", "").strip()
DUP_WINDOW_SEC = int(os.getenv("DUP_WINDOW_SEC", "90"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
RECEIPT_COOKIE_MAX_AGE_SEC = int(os.getenv("RECEIPT_COOKIE_MAX_AGE_SEC", "1800"))

router = APIRouter()

_dup_lock = threading.Lock()
_recent_submit_fingerprints: dict[str, float] = {}

# ============================
# CSRF Origin/Referer Validation
# ============================
_ALLOWED_ORIGINS: set[str] = set()

def _get_allowed_origins() -> set[str]:
    """Build allowed origins lazily from PUBLIC_BASE_URL config.

    P1-5 fix: Uses PUBLIC_BASE_URL (cloud's own public URL) instead of
    EXTERNAL_BASE (which is an edge-to-cloud variable). This ensures CSRF
    validation is tied to the correct domain.
    """
    global _ALLOWED_ORIGINS
    if not _ALLOWED_ORIGINS:
        if PUBLIC_BASE_URL:
            parsed = urlparse(PUBLIC_BASE_URL)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            _ALLOWED_ORIGINS.add(origin.lower())
    return _ALLOWED_ORIGINS

def _check_csrf(request: Request) -> None:
    """Validate Origin or Referer header for form POST.

    P1-5 fix: Fail-closed when Origin/Referer is present but no allowed
    origins are configured. Falls through without error only when both
    headers are absent (some LIFF WebViews strip these headers).
    """
    origin = (request.headers.get("origin") or "").strip().lower()
    referer = (request.headers.get("referer") or "").strip().lower()

    # If neither header is present, allow (LIFF may strip them)
    if not origin and not referer:
        return

    allowed = _get_allowed_origins()

    # P1-5: Fail-closed — if PUBLIC_BASE_URL is not configured but the browser
    # sent an Origin/Referer header, block the request rather than silently
    # disabling CSRF protection.
    if not allowed:
        logger.warning("[SEC][CSRF] Blocked request: PUBLIC_BASE_URL not configured but Origin/Referer present")
        raise HTTPException(status_code=403, detail="Request blocked by CSRF policy (server misconfigured)")

    if origin:
        if origin in allowed:
            return
        logger.warning(f"[SEC][CSRF] Blocked request with mismatched Origin: {origin}")
        raise HTTPException(status_code=403, detail="Request blocked by CSRF policy")

    if referer:
        parsed = urlparse(referer)
        referer_origin = f"{parsed.scheme}://{parsed.netloc}".lower()
        if referer_origin in allowed:
            return
        logger.warning(f"[SEC][CSRF] Blocked request with mismatched Referer origin: {referer_origin}")
        raise HTTPException(status_code=403, detail="Request blocked by CSRF policy")


def _is_recent_duplicate(fingerprint: str, now_ts: float) -> bool:
    with _dup_lock:
        expired_keys = [k for k, ts in _recent_submit_fingerprints.items() if ts < now_ts]
        for k in expired_keys:
            _recent_submit_fingerprints.pop(k, None)
        if fingerprint in _recent_submit_fingerprints:
            return True
        _recent_submit_fingerprints[fingerprint] = now_ts + DUP_WINDOW_SEC
        return False


def _safe_public_stub(stub: dict) -> dict:
    return public_stub_view(stub)


def _receipt_cookie_name(case_id: str) -> str:
    return f"case_receipt_{case_id.replace('-', '')}"


def _is_https_request(request: Request) -> bool:
    xfp = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if xfp:
        return xfp == "https"
    return request.url.scheme == "https"


def _set_receipt_cookie(response, request: Request, case_id: str, receipt: str) -> None:
    response.set_cookie(
        key=_receipt_cookie_name(case_id),
        value=receipt,
        max_age=RECEIPT_COOKIE_MAX_AGE_SEC,
        httponly=True,
        secure=_is_https_request(request),
        samesite="lax",
        path="/",
    )


def _has_valid_receipt(stored_receipt: str, query_receipt: str, cookie_receipt: str) -> bool:
    return receipt_matches(query_receipt, stored_receipt) or receipt_matches(cookie_receipt, stored_receipt)


# ============================
# Public-facing Routes（給家屬使用）
# ============================
@router.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
      <head><title>Diaper Rash AI</title></head>
      <body style="font-family: sans-serif; text-align: center; padding: 50px;">
        <h1>Diaper Rash AI Service</h1>
        <p>System Status: <span style='color:green'>Online</span></p>
        <a href='/form' style="background: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Submit Case</a>
      </body>
    </html>
    """

@router.get("/health")
def health_check():
    """Basic health check endpoint for container orchestration."""
    return {"status": "ok", "service": "cloud_public"}

@router.get("/form", response_class=HTMLResponse)
def form_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="form.html",
        context={"request": request, "liff_id": LIFF_ID},
        headers={"Cache-Control": "no-store"},
    )

@router.post("/submit_case")
async def submit_case(
    request: Request,
    name: str = Form(...),
    phone: str = Form(...),
    image: UploadFile = File(...),
    line_user_id: str = Form(...),
):
    # CSRF check
    _check_csrf(request)
    return await submit_case_workflow(
        request=request,
        name=name,
        phone=phone,
        image=image,
        line_user_id=line_user_id,
        is_recent_duplicate=_is_recent_duplicate,
    )

@router.get("/result/{case_id}", response_class=HTMLResponse)
def result_page(request: Request, case_id: str, r: Optional[str] = None):
    ip = get_client_ip(request)
    if not _rate_check(ip, RESULT_RATE_LIMIT, RESULT_RATE_WINDOW_SEC):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")

    case_id = normalize_case_id(case_id)
    sp = stub_path(case_id)

    headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}

    if not os.path.exists(sp):
        case = {"id": case_id, "status": "not_found"}
        return templates.TemplateResponse(
            request=request,
            name="result.html",
            context={"request": request, "case": case, "case_id": case_id, "r_ok": False},
            headers=headers,
        )

    stub = load_json(sp)
    stored_receipt = stub.get("receipt")
    query_receipt = (r or "").strip().lower()
    cookie_receipt = (request.cookies.get(_receipt_cookie_name(case_id)) or "").strip().lower()

    if not _has_valid_receipt(stored_receipt, query_receipt, cookie_receipt):
        limited_case = {"id": case_id, "status": "restricted"}
        return templates.TemplateResponse(
            request=request,
            name="result.html",
            context={"request": request, "case": limited_case, "case_id": case_id, "r_ok": False},
            headers=headers,
        )

    # If a receipt query param is present, store it in HttpOnly cookie and strip it from URL.
    if query_receipt:
        resp = RedirectResponse(url=f"/result/{case_id}", status_code=303)
        _set_receipt_cookie(resp, request, case_id, stored_receipt)
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Pragma"] = "no-cache"
        return resp

    safe_stub = _safe_public_stub(stub)
    resp = templates.TemplateResponse(
        request=request,
        name="result.html",
        context={"request": request, "case": safe_stub, "case_id": case_id, "r_ok": True},
        headers=headers,
    )
    _set_receipt_cookie(resp, request, case_id, stored_receipt)
    return resp

@router.get("/api/status/{case_id}")
def api_status(request: Request, case_id: str, r: Optional[str] = None):
    """給前端輪詢用（JSON）。"""
    ip = get_client_ip(request)
    if not _rate_check(ip, RESULT_RATE_LIMIT, RESULT_RATE_WINDOW_SEC):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")

    case_id = normalize_case_id(case_id)
    sp = stub_path(case_id)
    if not os.path.exists(sp):
        raise HTTPException(status_code=404, detail="Case not found")

    stub = load_json(sp)
    stored_receipt = stub.get("receipt")
    query_receipt = (r or "").strip().lower()
    cookie_receipt = (request.cookies.get(_receipt_cookie_name(case_id)) or "").strip().lower()

    if not _has_valid_receipt(stored_receipt, query_receipt, cookie_receipt):
        return {"id": case_id, "status": "restricted"}

    payload = _safe_public_stub(stub)
    if query_receipt and receipt_matches(query_receipt, stored_receipt):
        resp = JSONResponse(payload)
        _set_receipt_cookie(resp, request, case_id, stored_receipt)
        resp.headers["Cache-Control"] = "no-store"
        return resp
    return payload
