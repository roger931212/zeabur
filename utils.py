"""
Compatibility re-export layer (temporary).

DEPRECATION NOTE:
- This module is kept to preserve legacy `from utils import ...` imports.
- New code should import from the split modules directly:
  - utils_paths.py
  - utils_stub.py
  - utils_security.py
  - utils_rate_limit.py
  - utils_validation.py
- Planned migration: remove this shim after all imports are migrated.
"""

from utils_paths import (
    load_json,
    normalize_case_id,
    pending_path,
    processing_path,
    resolve_upload_path_safe,
    safe_read_file_limited,
    save_json_atomic,
    stub_path,
    upload_path,
)
from utils_rate_limit import _rate_check, _rate_hits, _rate_last_seen
from utils_security import (
    _cleanup_replay_cache,
    _is_trusted_proxy_ip,
    _replay_cache,
    get_client_ip,
    receipt_matches,
    security_headers,
    verify_internal_key,
    verify_internal_signature,
)
from utils_stub import (
    STUB_ALLOWED_STATUSES,
    STUB_DEFAULT_MESSAGE,
    STUB_PUBLIC_FIELDS,
    _normalize_ai_level,
    _normalize_iso_text,
    _normalize_optional_iso_text,
    _normalize_stub_status,
    create_stub,
    normalize_stub_payload,
    public_stub_view,
    update_stub_fields,
)
from utils_validation import (
    _normalize_phone_digits,
    _LINE_USER_ID_RE,
    detect_image_ext_from_magic,
    guess_ext,
    validate_line_user_id,
    validate_phone,
    validate_upload_content_type,
)

__all__ = [
    # paths/io
    "normalize_case_id",
    "stub_path",
    "pending_path",
    "processing_path",
    "upload_path",
    "resolve_upload_path_safe",
    "save_json_atomic",
    "load_json",
    "safe_read_file_limited",
    # stub
    "STUB_ALLOWED_STATUSES",
    "STUB_DEFAULT_MESSAGE",
    "STUB_PUBLIC_FIELDS",
    "_normalize_stub_status",
    "_normalize_iso_text",
    "_normalize_optional_iso_text",
    "_normalize_ai_level",
    "normalize_stub_payload",
    "create_stub",
    "public_stub_view",
    "update_stub_fields",
    # security
    "_is_trusted_proxy_ip",
    "get_client_ip",
    "_rate_check",
    "_cleanup_replay_cache",
    "verify_internal_key",
    "verify_internal_signature",
    "receipt_matches",
    "security_headers",
    "_replay_cache",
    "_rate_hits",
    "_rate_last_seen",
    # validation
    "guess_ext",
    "validate_upload_content_type",
    "detect_image_ext_from_magic",
    "_normalize_phone_digits",
    "validate_phone",
    "_LINE_USER_ID_RE",
    "validate_line_user_id",
]
