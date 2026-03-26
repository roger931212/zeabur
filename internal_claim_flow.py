import base64
import os
import re

from fastapi import HTTPException


_RECEIPT_RE = re.compile(r"^[0-9a-f]{32}$")


def claim_case_workflow_impl(
    request,
    *,
    verify_internal_signature,
    dirs,
    list_pending_files,
    extract_case_id_from_pending_path,
    move_pending_to_processing,
    pending_path,
    processing_path,
    load_json,
    purge_unidentified_pending,
    purge_case_files,
    resolve_upload_path_safe,
    safe_read_file_limited,
    max_claim_image_bytes: int,
    update_stub_fields,
    status_processing: str,
) -> dict:
    verify_internal_signature(request, b"")
    for _ in range(5):
        pending_files = list_pending_files(dirs["pending"])

        if not pending_files:
            return {"status": "empty"}

        target_file = pending_files[0]
        case_id = extract_case_id_from_pending_path(target_file)
        if not case_id:
            purge_unidentified_pending(target_file, "invalid_pending_filename")
            continue

        try:
            src_path, dest_path = move_pending_to_processing(
                case_id,
                pending_path=pending_path,
                processing_path=processing_path,
            )
        except FileNotFoundError:
            continue
        except Exception as e:
            record = None
            receipt = None
            try:
                record = load_json(src_path)
                raw_receipt = (record.get("receipt") or "").strip().lower()
                if _RECEIPT_RE.match(raw_receipt):
                    receipt = raw_receipt
            except Exception:
                pass
            purge_case_files(
                case_id=case_id,
                note="pending_to_processing_lock_failed",
                message="Case lock failed",
                receipt=receipt,
                pending_json_path=src_path,
                record=record,
                exc=e,
                status_code=500,
            )
            continue

        try:
            record = load_json(dest_path)
        except Exception as e:
            return purge_case_files(
                case_id=case_id,
                note="bad_processing_json",
                message="Bad case data",
                processing_json_path=dest_path,
                exc=e,
            )

        if record.get("id") != case_id:
            return purge_case_files(
                case_id=case_id,
                note="case_id_mismatch",
                message="Invalid case data",
                processing_json_path=dest_path,
                record=record,
            )

        receipt = (record.get("receipt") or "").strip().lower()
        if not _RECEIPT_RE.match(receipt):
            return purge_case_files(
                case_id=case_id,
                note="receipt_invalid",
                message="Invalid case data",
                processing_json_path=dest_path,
                record=record,
            )

        image_filename = (record.get("image_filename") or "").strip()
        img_path = resolve_upload_path_safe(image_filename)

        if (not img_path) or (not os.path.exists(img_path)):
            return purge_case_files(
                case_id=case_id,
                note="image_missing_or_invalid_path",
                message="Image missing",
                receipt=receipt,
                processing_json_path=dest_path,
                record=record,
            )

        try:
            if os.path.getsize(img_path) > max_claim_image_bytes:
                return purge_case_files(
                    case_id=case_id,
                    note="image_too_large",
                    message="Image too large",
                    receipt=receipt,
                    processing_json_path=dest_path,
                    record=record,
                    status_code=413,
                )
        except Exception as e:
            return purge_case_files(
                case_id=case_id,
                note="image_stat_failed",
                message="Image missing",
                receipt=receipt,
                processing_json_path=dest_path,
                record=record,
                exc=e,
            )

        try:
            img_bytes = safe_read_file_limited(img_path, max_claim_image_bytes)
        except HTTPException as e:
            return purge_case_files(
                case_id=case_id,
                note="image_read_failed_or_too_large",
                message="Image too large",
                receipt=receipt,
                processing_json_path=dest_path,
                record=record,
                exc=e,
                status_code=e.status_code,
            )
        except Exception as e:
            return purge_case_files(
                case_id=case_id,
                note="image_read_exception",
                message="Image missing",
                receipt=receipt,
                processing_json_path=dest_path,
                record=record,
                exc=e,
            )

        update_stub_fields(
            case_id,
            {
                "status": status_processing,
                "message": "AI 分析中，請稍候。",
            },
        )

        ext = os.path.splitext(image_filename)[1].lower() if image_filename else ".jpg"
        if ext == ".jpeg":
            ext = ".jpg"

        return {
            "status": "ok",
            "data": record,
            "image_b64": base64.b64encode(img_bytes).decode("utf-8"),
            "image_ext": ext,
        }

    return {"status": "empty"}
