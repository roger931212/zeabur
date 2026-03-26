def verify_stub_receipt(
    case_id: str,
    receipt: str,
    *,
    stub_path,
    load_json,
    normalize_stub_payload,
    receipt_matches,
):
    sp = stub_path(case_id)
    stub = normalize_stub_payload(load_json(sp), fallback_case_id=case_id)
    if not receipt_matches(receipt, stub.get("receipt")):
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Receipt mismatch")
    return stub


def purge_unidentified_pending(
    path: str,
    note: str,
    *,
    safe_file_size,
    safe_remove,
    log_zero_retention_error,
):
    json_size = safe_file_size(path)
    safe_remove(path)
    log_zero_retention_error(
        case_id="unknown",
        status_code=500,
        exc=ValueError("invalid_pending_filename"),
        note=note,
        json_size=json_size,
    )


def purge_case_files(
    *,
    case_id: str,
    note: str,
    message: str,
    receipt: str = None,
    pending_json_path=None,
    processing_json_path=None,
    record=None,
    exc=None,
    status_code: int = 500,
    resolve_upload_path_safe,
    safe_file_size,
    safe_remove,
    update_stub_fields,
    privacy_safe_retry_message: str,
    status_error: str,
    log_zero_retention_error,
):
    image_path = None
    if isinstance(record, dict):
        image_filename = (record.get("image_filename") or "").strip()
        if image_filename:
            image_path = resolve_upload_path_safe(image_filename)

    json_size = safe_file_size(processing_json_path) or safe_file_size(pending_json_path)
    image_size = safe_file_size(image_path)

    safe_remove(image_path)
    safe_remove(processing_json_path)
    safe_remove(pending_json_path)

    update_stub_fields(
        case_id,
        {
            "status": status_error,
            "message": privacy_safe_retry_message,
        },
    )
    log_zero_retention_error(
        case_id=case_id,
        status_code=status_code,
        exc=exc or RuntimeError(note),
        note=note,
        json_size=json_size,
        image_size=image_size,
    )

    resp = {"status": "error", "message": message, "case_id": case_id}
    if receipt:
        resp["receipt"] = receipt
    return resp
