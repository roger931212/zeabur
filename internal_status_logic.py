from fastapi import HTTPException

from constants import STATUS_DONE, STATUS_ERROR, STATUS_EXPIRED, STATUS_PENDING, STATUS_PROCESSING
from utils_stub import default_message_for_status


STATUS_ORDER = {
    STATUS_PENDING: 0,
    STATUS_PROCESSING: 1,
    STATUS_DONE: 2,
    STATUS_ERROR: 2,
    STATUS_EXPIRED: 2,
}


def normalize_status_for_transition(status: str) -> str:
    s = str(status or "").strip().lower()
    if s in STATUS_ORDER:
        return s
    return STATUS_ERROR


def validate_forward_only_transition(current_status: str, next_status: str) -> tuple[str, str]:
    current = normalize_status_for_transition(current_status)
    nxt = normalize_status_for_transition(next_status)

    if STATUS_ORDER[nxt] < STATUS_ORDER[current]:
        raise HTTPException(
            status_code=409,
            detail=f"Invalid status transition: {current} -> {nxt}",
        )
    return current, nxt


def normalize_status_message(status: str, message: str) -> str:
    text = (message or "").strip()
    if text:
        return text
    return default_message_for_status(status)


def normalize_ai_level_for_status(status: str, ai_level):
    if status == STATUS_DONE:
        return int(ai_level)
    return int(ai_level) if ai_level is not None else None
