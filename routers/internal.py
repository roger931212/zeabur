import re
import uuid
import os
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from constants import STATUS_DONE, STATUS_ERROR, STATUS_EXPIRED, STATUS_PENDING, STATUS_PROCESSING
from internal_workflow_service import (
    abort_case_workflow,
    claim_case_workflow,
    confirm_case_workflow,
    heartbeat_case_workflow,
    read_signed_payload,
    update_ai_result_workflow,
)
from utils_security import verify_internal_key


router = APIRouter()
_RECEIPT_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_AI_SUGGESTION_CHARS = int(os.getenv("MAX_AI_SUGGESTION_CHARS", "1200"))


class _StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReceiptPayload(_StrictPayload):
    case_id: str = Field(min_length=36, max_length=36)
    receipt: str = Field(min_length=32, max_length=64)

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, v: str) -> str:
        try:
            return str(uuid.UUID(v))
        except Exception:
            raise ValueError("Invalid case_id")

    @field_validator("receipt")
    @classmethod
    def validate_receipt(cls, v: str) -> str:
        s = (v or "").strip().lower()
        if not _RECEIPT_RE.match(s):
            raise ValueError("Invalid receipt")
        return s


class UpdateAiPayload(ReceiptPayload):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: str = Field(default="done", max_length=32)
    message: str = Field(default="", max_length=500)
    ai_level: Optional[int] = Field(default=None, ge=0, le=2)
    ai_suggestion: Optional[str] = Field(default=None, max_length=MAX_AI_SUGGESTION_CHARS)

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        s = (v or "").strip().lower()
        normalized = {
            "pending": STATUS_PENDING,
            "processing": STATUS_PROCESSING,
            "done": STATUS_DONE,
            "error": STATUS_ERROR,
            "expired": STATUS_EXPIRED,
        }.get(s)
        if not normalized:
            raise ValueError("Invalid status")
        return normalized


@router.post("/claim_case")
async def claim_case(
    request: Request,
    x_api_key: str = Depends(verify_internal_key),
):
    return claim_case_workflow(request)


@router.post("/confirm_case")
async def confirm_case(
    request: Request,
    x_api_key: str = Depends(verify_internal_key),
):
    payload = await read_signed_payload(request, ReceiptPayload)
    return confirm_case_workflow(payload)


@router.post("/heartbeat_case")
async def heartbeat_case(
    request: Request,
    x_api_key: str = Depends(verify_internal_key),
):
    payload = await read_signed_payload(request, ReceiptPayload)
    return heartbeat_case_workflow(payload)


@router.post("/update_ai_result")
async def update_ai_result(
    request: Request,
    x_api_key: str = Depends(verify_internal_key),
):
    payload = await read_signed_payload(request, UpdateAiPayload)
    return update_ai_result_workflow(payload)


@router.post("/abort_case")
async def abort_case(
    request: Request,
    x_api_key: str = Depends(verify_internal_key),
):
    payload = await read_signed_payload(request, ReceiptPayload)
    return abort_case_workflow(payload)
