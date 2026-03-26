import hashlib
import hmac
import ipaddress
import logging
import os
import threading
import time
from typing import Dict

from fastapi import Header, HTTPException, Request

from utils_rate_limit import _rate_check

logger = logging.getLogger("external")
INTERNAL_ALLOWED_IPS = {
    ip.strip() for ip in os.getenv("INTERNAL_ALLOWED_IPS", "").split(",") if ip.strip()
}
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "").strip()
INTERNAL_RATE_LIMIT = int(os.getenv("INTERNAL_RATE_LIMIT", "300"))
INTERNAL_RATE_WINDOW_SEC = int(os.getenv("INTERNAL_RATE_WINDOW_SEC", "600"))
INTERNAL_SIGNING_MAX_SKEW_SEC = int(os.getenv("INTERNAL_SIGNING_MAX_SKEW_SEC", "300"))
INTERNAL_SIGNING_SECRET = os.getenv("INTERNAL_SIGNING_SECRET", "").strip()
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "0").strip() == "1"
TRUST_X_FORWARDED_FOR = os.getenv("TRUST_X_FORWARDED_FOR", "0").strip() == "1"
TRUSTED_PROXY_IPS = {
    ip.strip() for ip in os.getenv("TRUSTED_PROXY_IPS", "").split(",") if ip.strip()
}
_TRUSTED_PROXY_NETWORKS = []
for _proxy in TRUSTED_PROXY_IPS:
    try:
        _TRUSTED_PROXY_NETWORKS.append(ipaddress.ip_network(_proxy, strict=False))
    except Exception:
        logger.warning(f"[SEC] Ignoring invalid TRUSTED_PROXY_IPS entry: {_proxy}")


def _is_trusted_proxy_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
        for network in _TRUSTED_PROXY_NETWORKS:
            if ip in network:
                return True
        return False
    except Exception:
        return False


def get_client_ip(request: Request) -> str:
    """
    Security policy:
    - Default to request.client.host.
    - Use proxy headers only when direct peer is trusted proxy.
    """
    direct_ip = request.client.host if request.client else "unknown"

    if not TRUST_PROXY_HEADERS:
        return direct_ip

    if not _is_trusted_proxy_ip(direct_ip):
        return direct_ip

    for h in ("cf-connecting-ip", "true-client-ip", "x-real-ip"):
        v = request.headers.get(h)
        if v:
            return v.strip()

    if TRUST_X_FORWARDED_FOR:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()

    return direct_ip


def verify_internal_key(
    request: Request,
    x_api_key: str = Header(..., alias="X-API-KEY"),
):
    # Fail-closed on server misconfiguration.
    if not INTERNAL_API_KEY:
        logger.error("[SEC] INTERNAL_API_KEY is not configured; rejecting internal API request")
        raise HTTPException(status_code=503, detail="Internal authentication not configured")

    # Optional IP allowlist
    if INTERNAL_ALLOWED_IPS:
        ip = get_client_ip(request)
        if ip not in INTERNAL_ALLOWED_IPS:
            logger.warning(f"[SEC] Blocked internal API call from non-allowlisted IP: {ip}")
            raise HTTPException(status_code=403, detail="Forbidden")

    # Constant-time API key check
    if not hmac.compare_digest(x_api_key, INTERNAL_API_KEY):
        logger.warning("[SEC] Invalid API Key attempt (masked)")
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Defence-in-depth rate limit for internal APIs
    ip = get_client_ip(request)
    if not _rate_check(ip, INTERNAL_RATE_LIMIT, INTERNAL_RATE_WINDOW_SEC):
        logger.warning(f"[SEC] Internal API rate limit exceeded from {ip}")
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    return x_api_key


_replay_cache: Dict[str, float] = {}
_replay_lock = threading.Lock()


def _cleanup_replay_cache(now_ts: float):
    expired = [k for k, v in _replay_cache.items() if v < now_ts]
    for k in expired:
        _replay_cache.pop(k, None)


def verify_internal_signature(request: Request, raw_body: bytes):
    # Fail-closed on server misconfiguration.
    if not INTERNAL_SIGNING_SECRET:
        logger.error("[SEC] INTERNAL_SIGNING_SECRET is not configured; rejecting signed internal request")
        raise HTTPException(status_code=503, detail="Internal signature verification not configured")

    ts_header = (request.headers.get("X-Internal-Timestamp") or "").strip()
    nonce = (request.headers.get("X-Internal-Nonce") or "").strip()
    signature = (request.headers.get("X-Internal-Signature") or "").strip().lower()

    if not ts_header or not nonce or not signature:
        raise HTTPException(status_code=401, detail="Missing signed headers")
    if len(nonce) > 128:
        raise HTTPException(status_code=401, detail="Invalid nonce")

    try:
        req_ts = int(ts_header)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid timestamp")

    now_ts = int(time.time())
    if abs(now_ts - req_ts) > INTERNAL_SIGNING_MAX_SKEW_SEC:
        raise HTTPException(status_code=401, detail="Expired signature")

    message = ts_header.encode("utf-8") + b"." + nonce.encode("utf-8") + b"." + (raw_body or b"")
    expected = hmac.new(
        INTERNAL_SIGNING_SECRET.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid signature")

    with _replay_lock:
        _cleanup_replay_cache(now_ts)
        if nonce in _replay_cache:
            raise HTTPException(status_code=401, detail="Replay detected")
        _replay_cache[nonce] = now_ts + INTERNAL_SIGNING_MAX_SKEW_SEC


def receipt_matches(a: str, b: str) -> bool:
    """Constant-time receipt comparison to prevent timing attacks."""
    if not a or not b:
        return False
    return hmac.compare_digest(str(a), str(b))


async def security_headers(request: Request, call_next):
    resp = await call_next(request)

    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://static.line-scdn.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data:; "
        "font-src 'self' https://cdn.jsdelivr.net; "
        "connect-src 'self' https://api.line.me; "
    )
    resp.headers["Strict-Transport-Security"] = "max-age=15552000; includeSubDomains"
    return resp
