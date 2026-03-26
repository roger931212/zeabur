import logging
import os
from datetime import datetime
from typing import Any, Optional

from constants import (
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_EXPIRED,
    STATUS_PENDING,
    STATUS_PROCESSING,
    VALID_STATUSES,
)
from utils_paths import load_json, save_json_atomic, stub_path

logger = logging.getLogger("external")


STUB_ALLOWED_STATUSES = VALID_STATUSES
STUB_DEFAULT_MESSAGE = "案件已建立，等待 AI 分析。"
STUB_PUBLIC_FIELDS = (
    "id",
    "created_at",
    "status",
    "message",
    "ai_level",
    "ai_suggestion",
    "ai_updated_at",
)

_STUB_STATUS_MAP = {
    "pending": STATUS_PENDING,
    "processing": STATUS_PROCESSING,
    "done": STATUS_DONE,
    "completed": STATUS_DONE,
    "success": STATUS_DONE,
    "error": STATUS_ERROR,
    "failed": STATUS_ERROR,
    "invalid": STATUS_ERROR,
    "rejected": STATUS_ERROR,
    "expired": STATUS_EXPIRED,
}

_STUB_DEFAULT_MESSAGE_BY_STATUS = {
    STATUS_PENDING: "案件已建立，等待 AI 分析。",
    STATUS_PROCESSING: "AI 分析中，請稍候。",
    STATUS_DONE: "分析完成。",
    STATUS_ERROR: "系統處理發生問題，請稍後再試。",
    STATUS_EXPIRED: "案件已過期。",
}


def _normalize_stub_status(status: Any) -> str:
    s = str(status or "").strip().lower()
    mapped = _STUB_STATUS_MAP.get(s)
    if mapped:
        return mapped
    if s in STUB_ALLOWED_STATUSES:
        return s
    return STATUS_PENDING


def default_message_for_status(status: str) -> str:
    normalized = _normalize_stub_status(status)
    defaults = {
        STATUS_PENDING: "案件已建立，等待 AI 分析。",
        STATUS_PROCESSING: "AI 分析中，請稍候。",
        STATUS_DONE: "分析完成",
        STATUS_ERROR: "AI 推論失敗，請稍後再試或改由人工審閱。",
        STATUS_EXPIRED: "案件已過期。",
    }
    return defaults.get(normalized, defaults[STATUS_PENDING])


def _normalize_iso_text(value: Any, fallback: Optional[str] = None) -> str:
    text = str(value or "").strip()
    if text:
        return text
    return fallback or datetime.now().isoformat(timespec="seconds")


def _normalize_optional_iso_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _normalize_optional_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _normalize_ai_level(value: Any) -> Optional[int]:
    """Normalize ai_level to 0/1/2. Returns None if input is None."""
    if value is None:
        return None
    try:
        n = int(value)
    except Exception:
        return 0
    if n < 0:
        return 0
    if n > 2:
        return 2
    return n


def normalize_stub_payload(
    data: Optional[dict],
    *,
    fallback_case_id: Optional[str] = None,
    fallback_receipt: Optional[str] = None,
) -> dict:
    raw = data if isinstance(data, dict) else {}
    status = _normalize_stub_status(raw.get("status"))
    message = str(raw.get("message") or "").strip() or _STUB_DEFAULT_MESSAGE_BY_STATUS[status]
    return {
        "id": str(raw.get("id") or fallback_case_id or ""),
        "receipt": str(raw.get("receipt") or fallback_receipt or ""),
        "created_at": _normalize_iso_text(raw.get("created_at")),
        "status": status,
        "message": message,
        "ai_level": _normalize_ai_level(raw.get("ai_level")) if status == STATUS_DONE else raw.get("ai_level"),
        "ai_suggestion": str(raw.get("ai_suggestion") or "").strip(),
        "ai_updated_at": _normalize_optional_iso_text(raw.get("ai_updated_at")),
        "expired_at": _normalize_optional_iso_text(raw.get("expired_at")),
        "expired_reason": _normalize_optional_text(raw.get("expired_reason")),
    }


def create_stub(
    *,
    case_id: str,
    receipt: str,
    created_at: Optional[str] = None,
    status: str = STATUS_PENDING,
    message: str = STUB_DEFAULT_MESSAGE,
    ai_level: int = 0,
    ai_suggestion: str = "",
    ai_updated_at: Optional[str] = None,
) -> dict:
    return normalize_stub_payload(
        {
            "id": case_id,
            "receipt": receipt,
            "created_at": created_at or datetime.now().isoformat(timespec="seconds"),
            "status": status,
            "message": message,
            "ai_level": ai_level,
            "ai_suggestion": ai_suggestion,
            "ai_updated_at": ai_updated_at,
        },
        fallback_case_id=case_id,
        fallback_receipt=receipt,
    )


def public_stub_view(stub: Optional[dict]) -> dict:
    normalized = normalize_stub_payload(stub)
    return {k: normalized[k] for k in STUB_PUBLIC_FIELDS}


def update_stub_fields(case_id: str, fields: dict):
    sp = stub_path(case_id)
    if not os.path.exists(sp):
        return
    try:
        stub = normalize_stub_payload(load_json(sp), fallback_case_id=case_id)
        stub.update(fields)
        save_json_atomic(sp, normalize_stub_payload(stub, fallback_case_id=case_id))
    except Exception as e:
        logger.error(f"Failed to update stub {case_id}: {e}")
