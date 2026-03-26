import hashlib
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse

from constants import PRIVACY_SAFE_RETRY_MESSAGE, log_zero_retention_error, safe_file_size, safe_remove
from utils_paths import pending_path, save_json_atomic, stub_path, upload_path
from utils_rate_limit import _rate_check
from utils_security import get_client_ip
from utils_stub import create_stub
from utils_validation import (
    detect_image_ext_from_magic,
    validate_line_user_id,
    validate_phone,
    validate_upload_content_type,
)

MAX_NAME_CHARS = int(os.getenv("MAX_NAME_CHARS", "80"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(8 * 1024 * 1024)))
SUBMIT_RATE_LIMIT = int(os.getenv("SUBMIT_RATE_LIMIT", "20"))
SUBMIT_RATE_WINDOW_SEC = int(os.getenv("SUBMIT_RATE_WINDOW_SEC", "600"))


@dataclass
class SubmitMeta:
    case_id: str
    receipt: str
    created_at: str
    name: str
    phone_digits: str
    line_user_id: str


@dataclass
class UploadResult:
    image_filename: str
    final_img_path: str
    tmp_path: str
    fingerprint: str


def _enforce_submit_rate_limit(request: Request) -> None:
    ip = get_client_ip(request)
    if not _rate_check(ip, SUBMIT_RATE_LIMIT, SUBMIT_RATE_WINDOW_SEC):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")


def _normalize_submit_meta(name: str, phone: str, line_user_id: str, image: UploadFile) -> SubmitMeta:
    normalized_name = (name or "").strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="Name is required")
    if len(normalized_name) > MAX_NAME_CHARS:
        normalized_name = normalized_name[:MAX_NAME_CHARS]

    phone_digits = validate_phone(phone)
    normalized_line_user_id = validate_line_user_id(line_user_id)
    if not validate_upload_content_type(image.content_type):
        raise HTTPException(status_code=400, detail="Invalid image content type")

    return SubmitMeta(
        case_id=str(uuid.uuid4()),
        receipt=uuid.uuid4().hex,
        created_at=datetime.now().isoformat(timespec="seconds"),
        name=normalized_name,
        phone_digits=phone_digits,
        line_user_id=normalized_line_user_id,
    )


async def _stream_upload_and_build_fingerprint(
    image: UploadFile,
    case_id: str,
    line_user_id: str,
    tmp_path: str,
) -> UploadResult:
    total = 0
    head = b""
    hasher = hashlib.sha256()
    final_img_path = None
    image_filename = None

    with open(tmp_path, "wb") as f:
        while True:
            chunk = await image.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="File too large")
            if len(head) < 32:
                head += chunk[: 32 - len(head)]
            hasher.update(chunk)
            f.write(chunk)

    if total <= 0:
        raise HTTPException(status_code=400, detail="Empty file is not allowed")

    detected_ext = detect_image_ext_from_magic(head)
    if not detected_ext:
        raise HTTPException(status_code=400, detail="Unsupported or invalid image file")

    image_filename = f"{case_id}{detected_ext}"
    final_img_path = upload_path(image_filename)
    os.replace(tmp_path, final_img_path)
    fingerprint = f"{line_user_id}:{hasher.hexdigest()}"

    return UploadResult(
        image_filename=image_filename,
        final_img_path=final_img_path,
        tmp_path=tmp_path,
        fingerprint=fingerprint,
    )


def _cleanup_failed_upload(case_id: str, upload_result: UploadResult, exc: Exception, note: str, status_code: int) -> None:
    upload_size = safe_file_size(upload_result.final_img_path) or safe_file_size(upload_result.tmp_path)
    safe_remove(upload_result.final_img_path)
    safe_remove(upload_result.tmp_path)
    log_zero_retention_error(
        case_id=case_id,
        status_code=status_code,
        exc=exc,
        note=note,
        upload_path_value=upload_result.final_img_path or upload_result.tmp_path,
        upload_size=upload_size,
    )


def _persist_case_and_stub(meta: SubmitMeta, image_filename: str) -> None:
    record = {
        "id": meta.case_id,
        "receipt": meta.receipt,
        "created_at": meta.created_at,
        "status": "pending",
        "name": meta.name,
        "phone": meta.phone_digits,
        "line_user_id": meta.line_user_id,
        "image_filename": image_filename,
    }
    stub = create_stub(
        case_id=meta.case_id,
        receipt=meta.receipt,
        created_at=meta.created_at,
        status="pending",
        message="案件已建立，等待 AI 分析。",
        ai_level=0,
        ai_suggestion="",
        ai_updated_at=None,
    )
    pending_file = pending_path(meta.case_id)
    stub_file = stub_path(meta.case_id)
    save_json_atomic(pending_file, record)
    save_json_atomic(stub_file, stub)


def _cleanup_failed_persist(meta: SubmitMeta, image_filename: str, exc: Exception) -> None:
    pending_file = pending_path(meta.case_id)
    stub_file = stub_path(meta.case_id)
    upload_file_path = upload_path(image_filename) if image_filename else None
    upload_size = safe_file_size(upload_file_path)
    pending_size = safe_file_size(pending_file)
    safe_remove(pending_file)
    safe_remove(stub_file)
    safe_remove(upload_file_path)
    log_zero_retention_error(
        case_id=meta.case_id,
        status_code=500,
        exc=exc,
        note="submit_case_persist_exception",
        upload_path_value=upload_file_path,
        pending_path_value=pending_file,
        upload_size=upload_size,
        pending_size=pending_size,
    )


async def submit_case_workflow(
    *,
    request: Request,
    name: str,
    phone: str,
    image: UploadFile,
    line_user_id: str,
    is_recent_duplicate: Callable[[str, float], bool],
) -> RedirectResponse:
    _enforce_submit_rate_limit(request)
    meta = _normalize_submit_meta(name, phone, line_user_id, image)

    upload_result = UploadResult(image_filename="", final_img_path="", tmp_path="", fingerprint="")
    tmp_path = upload_path(f"{meta.case_id}.upload")
    upload_result.tmp_path = tmp_path
    try:
        upload_result = await _stream_upload_and_build_fingerprint(
            image,
            meta.case_id,
            meta.line_user_id,
            tmp_path,
        )
        if is_recent_duplicate(upload_result.fingerprint, time.time()):
            raise HTTPException(status_code=429, detail="Duplicate submission detected. Please wait.")
    except HTTPException as e:
        _cleanup_failed_upload(meta.case_id, upload_result, e, "submit_case_upload_http_exception", e.status_code)
        raise
    except Exception as e:
        _cleanup_failed_upload(meta.case_id, upload_result, e, "submit_case_upload_exception", 500)
        raise HTTPException(status_code=500, detail=PRIVACY_SAFE_RETRY_MESSAGE)

    try:
        _persist_case_and_stub(meta, upload_result.image_filename)
    except Exception as e:
        _cleanup_failed_persist(meta, upload_result.image_filename, e)
        raise HTTPException(status_code=500, detail=PRIVACY_SAFE_RETRY_MESSAGE)

    return RedirectResponse(url=f"/result/{meta.case_id}?r={meta.receipt}", status_code=302)
