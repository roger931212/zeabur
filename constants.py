"""
Shared constants and small helper utilities for cloud_public.

All case status strings, zero-retention helpers, and common file
safety helpers are centralised here to avoid duplication across
routers/user.py and routers/internal.py.
"""

import os
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("external")

# ── Case Status Constants ──────────────────────────────────────────
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_EXPIRED = "expired"

VALID_STATUSES = {STATUS_PENDING, STATUS_PROCESSING, STATUS_DONE, STATUS_ERROR, STATUS_EXPIRED}

PRIVACY_SAFE_RETRY_MESSAGE = (
    "伺服器發生暫時性連線異常。為保護您的隱私安全，"
    "我們已中斷本次傳送並清除暫存檔案，請您重新填寫上傳。"
)


# ── File Safety Helpers ────────────────────────────────────────────
def safe_file_size(path: Optional[str]) -> Optional[int]:
    """Return file size in bytes, or None if unavailable."""
    if not path:
        return None
    try:
        if os.path.exists(path):
            return os.path.getsize(path)
    except Exception:
        pass
    return None


def safe_remove(path: Optional[str]) -> None:
    """Remove a file if it exists; never raise."""
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def log_zero_retention_error(
    *,
    case_id: str,
    status_code: int,
    exc: Exception,
    note: str,
    upload_path_value: Optional[str] = None,
    pending_path_value: Optional[str] = None,
    processing_path_value: Optional[str] = None,
    upload_size: Optional[int] = None,
    pending_size: Optional[int] = None,
    processing_size: Optional[int] = None,
    json_size: Optional[int] = None,
    image_size: Optional[int] = None,
) -> None:
    """Privacy-safe structured log for zero-retention error events."""
    logger.error(
        "[ZERO-RETENTION] ts=%s case_id=%s code=%s exc_type=%s note=%s "
        "upload_bytes=%s pending_bytes=%s processing_bytes=%s json_bytes=%s image_bytes=%s",
        datetime.now().isoformat(timespec="seconds"),
        case_id,
        status_code,
        type(exc).__name__,
        note,
        upload_size if upload_size is not None else safe_file_size(upload_path_value),
        pending_size if pending_size is not None else safe_file_size(pending_path_value),
        processing_size if processing_size is not None else safe_file_size(processing_path_value),
        json_size,
        image_size,
    )
