import re
from typing import Optional

from fastapi import HTTPException


def guess_ext(filename: str, content_type: str = None) -> str:
    fn = (filename or "").lower()
    if fn.endswith(".jpg") or fn.endswith(".jpeg"):
        return ".jpg"
    if fn.endswith(".png"):
        return ".png"
    ct = (content_type or "").lower()
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "png" in ct:
        return ".png"
    return ".jpg"


def validate_upload_content_type(content_type: Optional[str]) -> bool:
    ct = (content_type or "").lower().split(";")[0].strip()
    return ct in {"image/jpeg", "image/jpg", "image/png"}


def detect_image_ext_from_magic(head: bytes) -> Optional[str]:
    """Minimal image signature check to block disguised arbitrary uploads."""
    if not head:
        return None
    if head.startswith(b"\xFF\xD8\xFF"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    return None


def _normalize_phone_digits(phone: str) -> str:
    return re.sub(r"\D+", "", phone or "")


def validate_phone(phone: str) -> str:
    digits = _normalize_phone_digits(phone)
    if not (8 <= len(digits) <= 15):
        raise HTTPException(status_code=400, detail="Invalid phone format")
    return digits


_LINE_USER_ID_RE = re.compile(r"^U[0-9a-f]{32}$")


def validate_line_user_id(line_user_id: str) -> str:
    """Validate LIFF-provided LINE user ID."""
    s = (line_user_id or "").strip()
    if not s:
        raise HTTPException(status_code=400, detail="LINE User ID is required (please open from LINE app)")
    if not _LINE_USER_ID_RE.match(s):
        raise HTTPException(status_code=400, detail="Invalid LINE User ID format")
    return s
