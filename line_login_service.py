import logging

import requests
from fastapi import HTTPException

from config import (
    LINE_API_TIMEOUT_SEC,
    LINE_ID_TOKEN_VERIFY_URL,
    LINE_LOGIN_CHANNEL_ID,
    LINE_PROFILE_API_URL,
)

logger = logging.getLogger("external")


def _mask_user_id(user_id: str) -> str:
    s = (user_id or "").strip()
    if len(s) <= 6:
        return "***"
    return f"{s[:6]}***"


def _require_token(raw_value: str, *, field_name: str) -> str:
    token = (raw_value or "").strip()
    if token:
        return token
    raise HTTPException(
        status_code=400,
        detail=f"{field_name} is required (please open this form from LINE).",
    )


def _verify_id_token(id_token: str) -> str:
    try:
        resp = requests.post(
            LINE_ID_TOKEN_VERIFY_URL,
            data={
                "id_token": id_token,
                "client_id": LINE_LOGIN_CHANNEL_ID,
            },
            timeout=LINE_API_TIMEOUT_SEC,
        )
    except requests.RequestException as exc:
        logger.warning("[LINE_LOGIN] id_token verify request failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="LINE login verification service is temporarily unavailable.",
        )

    if resp.status_code != 200:
        logger.warning("[LINE_LOGIN] id_token verify rejected: status=%s", resp.status_code)
        raise HTTPException(status_code=401, detail="LINE identity verification failed.")

    try:
        payload = resp.json()
    except ValueError:
        logger.warning("[LINE_LOGIN] id_token verify returned invalid JSON")
        raise HTTPException(status_code=401, detail="LINE identity verification failed.")

    user_id = str(payload.get("sub") or "").strip()
    audience = str(payload.get("aud") or "").strip()
    if not user_id or audience != LINE_LOGIN_CHANNEL_ID:
        logger.warning(
            "[LINE_LOGIN] invalid id_token verify payload: aud_match=%s has_sub=%s",
            audience == LINE_LOGIN_CHANNEL_ID,
            bool(user_id),
        )
        raise HTTPException(status_code=401, detail="LINE identity verification failed.")
    return user_id


def _resolve_user_from_access_token(access_token: str) -> str:
    try:
        resp = requests.get(
            LINE_PROFILE_API_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=LINE_API_TIMEOUT_SEC,
        )
    except requests.RequestException as exc:
        logger.warning("[LINE_LOGIN] profile request failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="LINE login verification service is temporarily unavailable.",
        )

    if resp.status_code != 200:
        logger.warning("[LINE_LOGIN] profile request rejected: status=%s", resp.status_code)
        raise HTTPException(status_code=401, detail="LINE identity verification failed.")

    try:
        payload = resp.json()
    except ValueError:
        logger.warning("[LINE_LOGIN] profile request returned invalid JSON")
        raise HTTPException(status_code=401, detail="LINE identity verification failed.")

    user_id = str(payload.get("userId") or "").strip()
    if not user_id:
        logger.warning("[LINE_LOGIN] profile payload missing userId")
        raise HTTPException(status_code=401, detail="LINE identity verification failed.")
    return user_id


def verify_liff_identity(*, liff_id_token: str, liff_access_token: str) -> str:
    id_token = _require_token(liff_id_token, field_name="liff_id_token")
    access_token = _require_token(liff_access_token, field_name="liff_access_token")

    id_user_id = _verify_id_token(id_token)
    access_user_id = _resolve_user_from_access_token(access_token)
    if id_user_id != access_user_id:
        logger.warning(
            "[LINE_LOGIN] identity mismatch: id_token_user=%s access_token_user=%s",
            _mask_user_id(id_user_id),
            _mask_user_id(access_user_id),
        )
        raise HTTPException(status_code=401, detail="LINE identity verification failed.")

    return id_user_id
