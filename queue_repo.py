import glob
import os
import uuid


def list_pending_files(pending_dir: str) -> list[str]:
    pending_files = glob.glob(os.path.join(pending_dir, "*.json"))
    pending_files.sort(key=lambda p: (os.path.getmtime(p), os.path.basename(p)))
    return pending_files


def extract_case_id_from_pending_path(path: str) -> str | None:
    filename = os.path.basename(path)
    raw_case_id = os.path.splitext(filename)[0]
    try:
        return str(uuid.UUID(raw_case_id))
    except Exception:
        return None


def move_pending_to_processing(case_id: str, pending_path, processing_path) -> tuple[str, str]:
    src_path = pending_path(case_id)
    dest_path = processing_path(case_id)
    os.rename(src_path, dest_path)
    return src_path, dest_path
