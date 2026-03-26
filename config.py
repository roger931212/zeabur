import os
import logging
from fastapi.templating import Jinja2Templates

# ============================
# Logging
# ============================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("external")

# ============================
# 1) 基本設定（環境變數）
# ============================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "").strip()
if not INTERNAL_API_KEY:
    raise RuntimeError("INTERNAL_API_KEY is required")

INTERNAL_SIGNING_SECRET = os.getenv("INTERNAL_SIGNING_SECRET", "").strip()
if not INTERNAL_SIGNING_SECRET:
    raise RuntimeError("INTERNAL_SIGNING_SECRET is required")

INTERNAL_SIGNING_MAX_SKEW_SEC = int(os.getenv("INTERNAL_SIGNING_MAX_SKEW_SEC", "300"))

# 外網「內網 API」可選 IP 白名單（逗號分隔）
INTERNAL_ALLOWED_IPS = {
    ip.strip() for ip in os.getenv("INTERNAL_ALLOWED_IPS", "").split(",") if ip.strip()
}

# 上傳限制
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(8 * 1024 * 1024)))          # user->external
MAX_CLAIM_IMAGE_BYTES = int(os.getenv("MAX_CLAIM_IMAGE_BYTES", str(8 * 1024 * 1024))) # external->internal
MAX_NAME_CHARS = int(os.getenv("MAX_NAME_CHARS", "80"))
MAX_AI_SUGGESTION_CHARS = int(os.getenv("MAX_AI_SUGGESTION_CHARS", "1200"))

# Rate limit（in-memory；正式版建議改 Redis）
SUBMIT_RATE_LIMIT = int(os.getenv("SUBMIT_RATE_LIMIT", "20"))
SUBMIT_RATE_WINDOW_SEC = int(os.getenv("SUBMIT_RATE_WINDOW_SEC", "600"))  # 10 min
RESULT_RATE_LIMIT = int(os.getenv("RESULT_RATE_LIMIT", "120"))
RESULT_RATE_WINDOW_SEC = int(os.getenv("RESULT_RATE_WINDOW_SEC", "600"))  # 10 min
RATE_STORE_MAX = int(os.getenv("RATE_STORE_MAX", "10000"))  # 追蹤最多多少個 IP

# Internal API rate limit (per IP, defence-in-depth behind API key + HMAC)
INTERNAL_RATE_LIMIT = int(os.getenv("INTERNAL_RATE_LIMIT", "300"))
INTERNAL_RATE_WINDOW_SEC = int(os.getenv("INTERNAL_RATE_WINDOW_SEC", "600"))

# Duplicate submission window (seconds)
DUP_WINDOW_SEC = int(os.getenv("DUP_WINDOW_SEC", "90"))

LIFF_ID = os.getenv("LIFF_ID", "").strip()

# Public base URL for CSRF origin validation (P1-5).
# This is the cloud_public service's own public URL, NOT the edge service URL.
# If unset, CSRF protection will fail-closed on form POST when Origin/Referer is present.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")

# 保留政策（秒）
CLEANUP_INTERVAL_SEC = int(os.getenv("CLEANUP_INTERVAL_SEC", "300"))
PENDING_TTL_SEC = int(os.getenv("PENDING_TTL_SEC", "86400"))        # 24h
PROCESSING_TTL_SEC = int(os.getenv("PROCESSING_TTL_SEC", "86400"))  # 24h
UPLOAD_ORPHAN_TTL_SEC = int(os.getenv("UPLOAD_ORPHAN_TTL_SEC", "86400"))

# 可信代理（反代）設定：僅在明確設定 TRUSTED_PROXY_IPS 後建議啟用
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "0").strip() == "1"
TRUSTED_PROXY_IPS = {
    ip.strip() for ip in os.getenv("TRUSTED_PROXY_IPS", "").split(",") if ip.strip()
}
# 只在可信代理時才會讀 x-forwarded-for；預設關閉（避免被偽造）
TRUST_X_FORWARDED_FOR = os.getenv("TRUST_X_FORWARDED_FOR", "0").strip() == "1"

# ============================
# 2) 資料夾結構
# ============================
# uploads: 原始圖片 (Confirm 後刪除)
# pending: 完整案件 JSON (等待中)
# processing: 內網正在抓取中（鎖定狀態），等待 Confirm 刪除
# stubs: 僅存狀態與結果，不含個資，永久保留
DIRS = {
    "uploads": os.path.join(BASE_DIR, "storage", "uploads"),
    "pending": os.path.join(BASE_DIR, "storage", "pending"),
    "processing": os.path.join(BASE_DIR, "storage", "processing"),
    "stubs": os.path.join(BASE_DIR, "storage", "stubs"),
}
for d in DIRS.values():
    os.makedirs(d, exist_ok=True)

CLEANUP_LOCK_PATH = os.path.join(BASE_DIR, ".cleanup_worker.lock")
RUN_CLEANUP_WORKER = os.getenv("RUN_CLEANUP_WORKER", "1").strip() == "1"

# Templates
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
