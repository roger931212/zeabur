import glob
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime

from utils_paths import DIRS, resolve_upload_path_safe
from utils_stub import update_stub_fields
from constants import STATUS_EXPIRED

logger = logging.getLogger("external")

CLEANUP_INTERVAL_SEC = int(os.getenv("CLEANUP_INTERVAL_SEC", "300"))
PENDING_TTL_SEC = int(os.getenv("PENDING_TTL_SEC", "86400"))
PROCESSING_TTL_SEC = int(os.getenv("PROCESSING_TTL_SEC", "86400"))
UPLOAD_ORPHAN_TTL_SEC = int(os.getenv("UPLOAD_ORPHAN_TTL_SEC", "86400"))
PROCESSING_HEARTBEAT_TIMEOUT_SEC = int(os.getenv("PROCESSING_HEARTBEAT_TIMEOUT_SEC", "900"))
PROCESSING_FORCE_PURGE_SEC = int(os.getenv("PROCESSING_FORCE_PURGE_SEC", "604800"))

stop_event = threading.Event()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_unlink(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        logger.warning(f"[CLEANUP] Failed to remove file: {e}")


def _load_json_safely(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _extract_case_id(path: str):
    stem = os.path.splitext(os.path.basename(path))[0]
    try:
        return str(uuid.UUID(stem))
    except Exception:
        return None


def _parse_iso_to_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except Exception:
        return None


def _delete_linked_image(record: dict, case_id: str = None):
    image_filename = (record or {}).get("image_filename")
    if not image_filename:
        return
    image_path = resolve_upload_path_safe(image_filename)
    if not image_path:
        # Log only case_id, never raw image_filename (audit #7)
        logger.warning(f"[CLEANUP][SEC] Skip invalid image path for case_id={case_id or 'unknown'}")
        return
    _safe_unlink(image_path)


def _purge_json_dir(dir_key: str, ttl_sec: int, reason: str):
    if ttl_sec <= 0:
        return 0

    now_ts = time.time()
    removed = 0
    pattern = os.path.join(DIRS[dir_key], "*.json")

    for path in glob.glob(pattern):
        try:
            age = now_ts - os.path.getmtime(path)
        except Exception:
            continue

        if age < ttl_sec:
            continue

        record = _load_json_safely(path)
        case_id = _extract_case_id(path)

        _delete_linked_image(record, case_id=case_id)
        _safe_unlink(path)
        removed += 1

        if case_id:
            update_stub_fields(
                case_id,
                {
                    "status": STATUS_EXPIRED,
                    "expired_at": _now_iso(),
                    "expired_reason": reason,
                },
            )

    if removed:
        logger.info(f"[CLEANUP] Purged {removed} stale files from {dir_key} (reason={reason})")
    return removed


def _processing_is_stale(record: dict, now_ts: float) -> tuple[bool, str]:
    """Delete processing payload only when lease is expired and heartbeat is stale.

    Safety rule:
    - never purge active jobs based only on file age;
    - require expired lease or stale heartbeat;
    - optional hard cap (PROCESSING_FORCE_PURGE_SEC) for abandoned payloads.
    """
    lease_expires_ts = _parse_iso_to_ts((record or {}).get("lease_expires_at"))
    heartbeat_ts = _parse_iso_to_ts((record or {}).get("last_heartbeat_at"))
    claimed_ts = _parse_iso_to_ts((record or {}).get("claimed_at"))

    if lease_expires_ts and now_ts <= lease_expires_ts:
        return False, ""
    if heartbeat_ts and now_ts - heartbeat_ts <= PROCESSING_HEARTBEAT_TIMEOUT_SEC:
        return False, ""
    if heartbeat_ts is None and claimed_ts and now_ts - claimed_ts <= PROCESSING_HEARTBEAT_TIMEOUT_SEC:
        return False, ""

    if lease_expires_ts:
        return True, "processing_stale_lease_expired"
    if heartbeat_ts:
        return True, "processing_stale_heartbeat_timeout"
    return True, "processing_stale_no_heartbeat"


def _purge_processing_dir(ttl_sec: int):
    if ttl_sec <= 0:
        return 0

    now_ts = time.time()
    removed = 0
    pattern = os.path.join(DIRS["processing"], "*.json")

    for path in glob.glob(pattern):
        try:
            age = now_ts - os.path.getmtime(path)
        except Exception:
            continue

        if age < ttl_sec:
            continue

        record = _load_json_safely(path)
        case_id = _extract_case_id(path)
        stale, reason = _processing_is_stale(record, now_ts)
        if not stale:
            continue

        if PROCESSING_FORCE_PURGE_SEC > 0 and age >= PROCESSING_FORCE_PURGE_SEC:
            reason = "processing_force_ttl"

        _delete_linked_image(record, case_id=case_id)
        _safe_unlink(path)
        removed += 1

        if case_id:
            update_stub_fields(
                case_id,
                {
                    "status": STATUS_EXPIRED,
                    "expired_at": _now_iso(),
                    "expired_reason": reason,
                },
            )

    if removed:
        logger.info(f"[CLEANUP] Purged {removed} stale files from processing")
    return removed


def _purge_orphan_uploads(ttl_sec: int):
    if ttl_sec <= 0:
        return 0

    now_ts = time.time()
    removed = 0
    pattern = os.path.join(DIRS["uploads"], "*")
    for path in glob.glob(pattern):
        if not os.path.isfile(path):
            continue
        try:
            age = now_ts - os.path.getmtime(path)
        except Exception:
            continue
        if age < ttl_sec:
            continue
        _safe_unlink(path)
        removed += 1

    if removed:
        logger.info(f"[CLEANUP] Purged {removed} stale orphan uploads")
    return removed


def cleanup_once():
    _purge_json_dir("pending", PENDING_TTL_SEC, "pending_ttl")
    _purge_processing_dir(PROCESSING_TTL_SEC)
    _purge_orphan_uploads(UPLOAD_ORPHAN_TTL_SEC)


def cleanup_worker():
    interval = max(5, CLEANUP_INTERVAL_SEC)
    logger.info(f"[CLEANUP] Worker started (interval={interval}s)")

    while not stop_event.is_set():
        try:
            cleanup_once()
        except Exception as e:
            logger.error(f"[CLEANUP] Worker exception: {e}")
        stop_event.wait(interval)

    logger.info("[CLEANUP] Worker stopped")
