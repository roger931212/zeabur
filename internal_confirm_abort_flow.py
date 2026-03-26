import os

from fastapi import HTTPException


def confirm_case_workflow_impl(
    payload,
    *,
    normalize_case_id,
    processing_path,
    pending_path,
    load_json,
    receipt_matches,
    verify_stub_receipt,
    status_done: str,
    status_error: str,
    status_expired: str,
    status_pending: str,
    status_processing: str,
    update_stub_fields,
    privacy_safe_retry_message: str,
    purge_case_files,
    resolve_upload_path_safe,
    stub_path,
    normalize_stub_payload,
) -> dict:
    case_id = normalize_case_id(payload.case_id)
    receipt = payload.receipt

    proc = processing_path(case_id)
    if not os.path.exists(proc):
        pend = pending_path(case_id)
        if os.path.exists(pend):
            pending_record = load_json(pend)
            if not receipt_matches(receipt, pending_record.get("receipt")):
                raise HTTPException(status_code=403, detail="Receipt mismatch")
            raise HTTPException(status_code=409, detail="Case not claimed yet")

        stub = verify_stub_receipt(case_id, receipt)
        stub_status = stub.get("status")
        if stub_status in {status_done, status_error, status_expired}:
            return {"status": "ok", "message": "Already confirmed or not found (idempotent)"}

        if stub_status in {status_pending, status_processing}:
            update_stub_fields(
                case_id,
                {
                    "status": status_error,
                    "message": privacy_safe_retry_message,
                },
            )
            return {"status": "ok", "message": "Missing processing payload; stub marked error"}

        return {"status": "ok", "message": "Already confirmed or not found (idempotent)"}

    try:
        record = load_json(proc)
    except Exception as e:
        purge_case_files(
            case_id=case_id,
            note="confirm_bad_processing_json",
            message="Invalid case data",
            receipt=receipt,
            processing_json_path=proc,
            exc=e,
            status_code=500,
        )
        raise HTTPException(status_code=500, detail="Confirm failed after secure cleanup")
    if not receipt_matches(receipt, record.get("receipt")):
        raise HTTPException(status_code=403, detail="Receipt mismatch")

    try:
        image_filename = (record.get("image_filename") or "").strip()
        img_path = resolve_upload_path_safe(image_filename) if image_filename else None
        if img_path and os.path.exists(img_path):
            os.remove(img_path)
        if os.path.exists(proc):
            os.remove(proc)
    except Exception as e:
        purge_case_files(
            case_id=case_id,
            note="confirm_delete_failed",
            message="Confirm failed",
            receipt=receipt,
            processing_json_path=proc,
            record=record,
            exc=e,
            status_code=500,
        )
        raise HTTPException(status_code=500, detail="Confirm failed after secure cleanup")

    sp = stub_path(case_id)
    if os.path.exists(sp):
        try:
            stub = normalize_stub_payload(load_json(sp), fallback_case_id=case_id)
            if stub.get("status") in {status_done, status_error, status_expired}:
                return {"status": "ok"}
        except Exception:
            pass

    update_stub_fields(
        case_id,
        {
            "status": status_processing,
            "message": "AI 分析中，請稍候。",
        },
    )
    return {"status": "ok"}


def abort_case_workflow_impl(
    payload,
    *,
    normalize_case_id,
    processing_path,
    pending_path,
    load_json,
    receipt_matches,
    verify_stub_receipt,
    update_stub_fields,
    purge_case_files,
    status_pending: str,
) -> dict:
    case_id = normalize_case_id(payload.case_id)
    receipt = payload.receipt

    proc = processing_path(case_id)
    if not os.path.exists(proc):
        pend = pending_path(case_id)
        if os.path.exists(pend):
            pending_record = load_json(pend)
            if not receipt_matches(receipt, pending_record.get("receipt")):
                raise HTTPException(status_code=403, detail="Receipt mismatch")
            return {"status": "ok", "message": "Already pending"}

        verify_stub_receipt(case_id, receipt)
        return {"status": "ok", "message": "Nothing to abort"}

    try:
        record = load_json(proc)
    except Exception as e:
        purge_case_files(
            case_id=case_id,
            note="abort_bad_processing_json",
            message="Abort failed",
            receipt=receipt,
            processing_json_path=proc,
            exc=e,
            status_code=500,
        )
        raise HTTPException(status_code=500, detail="Abort failed after secure cleanup")

    if not receipt_matches(receipt, record.get("receipt")):
        raise HTTPException(status_code=403, detail="Receipt mismatch")

    try:
        os.rename(proc, pending_path(case_id))
    except Exception as e:
        purge_case_files(
            case_id=case_id,
            note="abort_rename_failed",
            message="Abort failed",
            receipt=receipt,
            processing_json_path=proc,
            record=record,
            exc=e,
            status_code=500,
        )
        raise HTTPException(status_code=500, detail="Abort failed after secure cleanup")

    update_stub_fields(
        case_id,
        {
            "status": status_pending,
            "message": "案件已建立，等待 AI 分析。",
        },
    )
    return {"status": "ok"}
