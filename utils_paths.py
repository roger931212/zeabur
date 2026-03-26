import json
import os
import uuid
from typing import Optional

from fastapi import HTTPException

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIRS = {
    "uploads": os.path.join(BASE_DIR, "storage", "uploads"),
    "pending": os.path.join(BASE_DIR, "storage", "pending"),
    "processing": os.path.join(BASE_DIR, "storage", "processing"),
    "stubs": os.path.join(BASE_DIR, "storage", "stubs"),
}


def normalize_case_id(case_id: str) -> str:
    """Force case_id into UUID canonical string, else 404."""
    try:
        return str(uuid.UUID(case_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Case not found")


def stub_path(case_id: str) -> str:
    return os.path.join(DIRS["stubs"], f"{case_id}.json")


def pending_path(case_id: str) -> str:
    return os.path.join(DIRS["pending"], f"{case_id}.json")


def processing_path(case_id: str) -> str:
    return os.path.join(DIRS["processing"], f"{case_id}.json")


def upload_path(filename: str) -> str:
    return os.path.join(DIRS["uploads"], filename)


def resolve_upload_path_safe(filename: str) -> Optional[str]:
    """Resolve upload path safely and ensure it stays under uploads dir."""
    name = (filename or "").strip()
    if not name:
        return None
    if os.path.basename(name) != name:
        return None

    uploads_root = os.path.realpath(DIRS["uploads"])
    candidate = os.path.realpath(os.path.join(DIRS["uploads"], name))
    if candidate != uploads_root and not candidate.startswith(uploads_root + os.sep):
        return None
    return candidate


def save_json_atomic(path: str, data: dict):
    """
    Atomic JSON write via temp-file + os.replace.
    NOTE: Requires temp file and target to be on the same filesystem/mount.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{uuid.uuid4().hex}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_json(path: str) -> dict:
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Data not found")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def safe_read_file_limited(path: str, max_bytes: int) -> bytes:
    size = os.path.getsize(path)
    if size > max_bytes:
        raise HTTPException(status_code=413, detail="Image too large")
    with open(path, "rb") as f:
        data = f.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail="Image too large")
    return data
