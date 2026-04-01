"""
Internal machine-to-machine workflow orchestration for cloud routes.

This module intentionally centralizes `/claim_case`, `/confirm_case`,
`/update_ai_result`, and `/abort_case` business logic so that
`routers/internal.py` stays as a thin validation/transport layer.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Optional, Type

from fastapi import HTTPException, Request
from pydantic import BaseModel, ValidationError

from constants import (
    PRIVACY_SAFE_RETRY_MESSAGE,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_EXPIRED,
    STATUS_PENDING,
    STATUS_PROCESSING,
    log_zero_retention_error,
    safe_file_size,
    safe_remove,
)
from internal_case_ops import (
    purge_case_files,
    purge_unidentified_pending,
    verify_stub_receipt,
)
from internal_claim_flow import claim_case_workflow_impl
from internal_confirm_abort_flow import abort_case_workflow_impl, confirm_case_workflow_impl
from internal_status_logic import (
    normalize_ai_level_for_status,
    normalize_status_message,
    normalize_status_for_transition,
    validate_forward_only_transition,
)
from queue_repo import (
    extract_case_id_from_pending_path,
    list_pending_files,
    move_pending_to_processing,
)
from utils_paths import (
    DIRS,
    load_json,
    normalize_case_id,
    pending_path,
    processing_path,
    resolve_upload_path_safe,
    save_json_atomic,
    safe_read_file_limited,
    stub_path,
)
from utils_security import receipt_matches, verify_internal_signature
from utils_stub import normalize_stub_payload, update_stub_fields

MAX_CLAIM_IMAGE_BYTES = int(os.getenv("MAX_CLAIM_IMAGE_BYTES", str(8 * 1024 * 1024)))
PROCESSING_LEASE_TIMEOUT_SEC = int(os.getenv("PROCESSING_LEASE_TIMEOUT_SEC", "900"))


async def read_signed_payload(request: Request, model: Type[BaseModel]) -> BaseModel:
    raw_body = await request.body()
    verify_internal_signature(request, raw_body)
    if not raw_body:
        raise HTTPException(status_code=400, detail="Missing JSON payload")
    try:
        data = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    try:
        return model.model_validate(data)
    except ValidationError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")


def _verify_stub_receipt(case_id: str, receipt: str) -> dict:
    if not os.path.exists(stub_path(case_id)):
        raise HTTPException(status_code=404, detail="Case not found")
    return verify_stub_receipt(
        case_id,
        receipt,
        stub_path=stub_path,
        load_json=load_json,
        normalize_stub_payload=normalize_stub_payload,
        receipt_matches=receipt_matches,
    )


def _purge_unidentified_pending(path: str, note: str) -> None:
    purge_unidentified_pending(
        path,
        note,
        safe_file_size=safe_file_size,
        safe_remove=safe_remove,
        log_zero_retention_error=log_zero_retention_error,
    )


def _purge_case_files(
    *,
    case_id: str,
    note: str,
    message: str,
    receipt: str = None,
    pending_json_path: Optional[str] = None,
    processing_json_path: Optional[str] = None,
    record: Optional[dict] = None,
    exc: Optional[Exception] = None,
    status_code: int = 500,
) -> dict:
    return purge_case_files(
        case_id=case_id,
        note=note,
        message=message,
        receipt=receipt,
        pending_json_path=pending_json_path,
        processing_json_path=processing_json_path,
        record=record,
        exc=exc,
        status_code=status_code,
        resolve_upload_path_safe=resolve_upload_path_safe,
        safe_file_size=safe_file_size,
        safe_remove=safe_remove,
        update_stub_fields=update_stub_fields,
        privacy_safe_retry_message=PRIVACY_SAFE_RETRY_MESSAGE,
        status_error=STATUS_ERROR,
        log_zero_retention_error=log_zero_retention_error,
    )


def claim_case_workflow(request: Request) -> dict:
    return claim_case_workflow_impl(
        request,
        verify_internal_signature=verify_internal_signature,
        dirs=DIRS,
        list_pending_files=list_pending_files,
        extract_case_id_from_pending_path=extract_case_id_from_pending_path,
        move_pending_to_processing=move_pending_to_processing,
        pending_path=pending_path,
        processing_path=processing_path,
        load_json=load_json,
        purge_unidentified_pending=_purge_unidentified_pending,
        purge_case_files=_purge_case_files,
        resolve_upload_path_safe=resolve_upload_path_safe,
        safe_read_file_limited=safe_read_file_limited,
        max_claim_image_bytes=MAX_CLAIM_IMAGE_BYTES,
        update_stub_fields=update_stub_fields,
        status_processing=STATUS_PROCESSING,
        save_json_atomic=save_json_atomic,
        processing_lease_timeout_sec=PROCESSING_LEASE_TIMEOUT_SEC,
    )


def heartbeat_case_workflow(payload) -> dict:
    case_id = normalize_case_id(payload.case_id)
    receipt = payload.receipt
    proc = processing_path(case_id)

    if not os.path.exists(proc):
        # Idempotent heartbeat: if processing payload is gone, confirm caller still owns case context.
        _verify_stub_receipt(case_id, receipt)
        return {"status": "ok", "message": "processing_missing"}

    record = load_json(proc)
    if not receipt_matches(receipt, record.get("receipt")):
        raise HTTPException(status_code=403, detail="Receipt mismatch")

    now = datetime.now()
    now_iso = now.isoformat(timespec="seconds")
    record.setdefault("claimed_at", now_iso)
    record["last_heartbeat_at"] = now_iso
    record["lease_expires_at"] = (now + timedelta(seconds=max(60, PROCESSING_LEASE_TIMEOUT_SEC))).isoformat(
        timespec="seconds"
    )
    save_json_atomic(proc, record)

    return {"status": "ok"}


def confirm_case_workflow(payload) -> dict:
    return confirm_case_workflow_impl(
        payload,
        normalize_case_id=normalize_case_id,
        processing_path=processing_path,
        pending_path=pending_path,
        load_json=load_json,
        receipt_matches=receipt_matches,
        verify_stub_receipt=_verify_stub_receipt,
        status_done=STATUS_DONE,
        status_error=STATUS_ERROR,
        status_expired=STATUS_EXPIRED,
        status_pending=STATUS_PENDING,
        status_processing=STATUS_PROCESSING,
        update_stub_fields=update_stub_fields,
        privacy_safe_retry_message=PRIVACY_SAFE_RETRY_MESSAGE,
        purge_case_files=_purge_case_files,
        resolve_upload_path_safe=resolve_upload_path_safe,
        stub_path=stub_path,
        normalize_stub_payload=normalize_stub_payload,
    )


def update_ai_result_workflow(payload) -> dict:
    case_id = normalize_case_id(payload.case_id)
    receipt = payload.receipt

    sp = stub_path(case_id)
    if not os.path.exists(sp):
        raise HTTPException(status_code=404, detail="Case not found")
    stub = normalize_stub_payload(load_json(sp), fallback_case_id=case_id)
    if not receipt_matches(receipt, stub.get("receipt")):
        raise HTTPException(status_code=403, detail="Receipt mismatch")

    current_status = normalize_status_for_transition(stub.get("status") or STATUS_PENDING)
    status = normalize_status_for_transition(payload.status)
    validate_forward_only_transition(current_status, status)

    if status == STATUS_DONE and payload.ai_level is None:
        raise HTTPException(status_code=400, detail="ai_level is required when status=done")

    message = normalize_status_message(status, payload.message)

    ai_level = normalize_ai_level_for_status(status, payload.ai_level)

    update_stub_fields(
        case_id,
        {
            "status": status,
            "message": message,
            "ai_level": ai_level,
            "ai_suggestion": (payload.ai_suggestion or "").strip(),
            "ai_updated_at": datetime.now().isoformat(timespec="seconds"),
        },
    )

    return {"status": "ok"}


def abort_case_workflow(payload) -> dict:
    return abort_case_workflow_impl(
        payload,
        normalize_case_id=normalize_case_id,
        processing_path=processing_path,
        pending_path=pending_path,
        load_json=load_json,
        receipt_matches=receipt_matches,
        verify_stub_receipt=_verify_stub_receipt,
        update_stub_fields=update_stub_fields,
        purge_case_files=_purge_case_files,
        status_pending=STATUS_PENDING,
    )
