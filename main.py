from fastapi import FastAPI
import logging
import os
import sys
import threading
from contextlib import asynccontextmanager

import config as _config  # Force startup-time required env validation.
from utils_security import security_headers
from routers import user, internal
from cleanup_worker import cleanup_worker, stop_event as cleanup_stop_event

logger = logging.getLogger("external")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLEANUP_LOCK_PATH = os.path.join(BASE_DIR, ".cleanup_worker.lock")
RUN_CLEANUP_WORKER = os.getenv("RUN_CLEANUP_WORKER", "1").strip() == "1"
REQUIRE_CLEANUP_WORKER = os.getenv("REQUIRE_CLEANUP_WORKER", "1").strip() == "1"
FILE_QUEUE_SINGLE_INSTANCE_REQUIRED = os.getenv("FILE_QUEUE_SINGLE_INSTANCE_REQUIRED", "1").strip() == "1"
REPLICA_ENV_KEYS = (
    "CLOUD_PUBLIC_REPLICA_COUNT",
    "ZEABUR_REPLICA_COUNT",
    "REPLICA_COUNT",
    "REPLICAS",
    "INSTANCE_COUNT",
)


def _detect_replica_signals() -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in REPLICA_ENV_KEYS:
        raw = (os.getenv(key) or "").strip()
        if not raw:
            continue
        try:
            counts[key] = int(raw)
        except ValueError:
            logger.warning(f"[DEPLOYMENT][WARN] Ignore non-integer replica signal {key}={raw!r}")
    return counts


def _enforce_file_queue_safety() -> None:
    if REQUIRE_CLEANUP_WORKER and not RUN_CLEANUP_WORKER:
        raise RuntimeError(
            "Unsafe startup: RUN_CLEANUP_WORKER=0 while REQUIRE_CLEANUP_WORKER=1. "
            "For filesystem queue mode, cleanup worker must stay enabled."
        )

    if not FILE_QUEUE_SINGLE_INSTANCE_REQUIRED:
        logger.warning(
            "[DEPLOYMENT][CRITICAL] FILE_QUEUE_SINGLE_INSTANCE_REQUIRED=0. "
            "This is unsafe for local filesystem queue mode."
        )
        return

    signals = _detect_replica_signals()
    if not signals:
        logger.warning(
            "[DEPLOYMENT][CRITICAL] File queue uses local disk and cannot verify replica count automatically. "
            "You MUST deploy exactly one cloud_public replica. "
            "Set CLOUD_PUBLIC_REPLICA_COUNT=1 to make this explicit."
        )
        return

    replica_count = max(signals.values())
    if replica_count > 1:
        raise RuntimeError(
            "Unsafe deployment: cloud_public file queue requires single instance (replica=1). "
            f"Detected replica signals={signals}. Multi-instance causes queue split."
        )
    if replica_count <= 0:
        raise RuntimeError(f"Invalid replica count detected: {signals}")


def _is_process_alive(pid: int) -> bool:
    """Cross-platform check if a process is alive (P1-8 fix).

    On Windows, os.kill(pid, 0) does not work as expected — it can
    raise PermissionError or terminate the process depending on version.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # process exists but we can't signal it
        except Exception:
            return False


# ============================
# FastAPI Application
# ============================
@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_thread = None
    lock_fd = None
    _enforce_file_queue_safety()

    def _acquire_lock(path: str):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            return fd
        except FileExistsError:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    pid = int((f.read() or "0").strip())
                if not _is_process_alive(pid):
                    raise ProcessLookupError("stale lock")
            except (ProcessLookupError, ValueError):
                try:
                    os.remove(path)
                except Exception:
                    return None
                try:
                    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(fd, str(os.getpid()).encode("utf-8"))
                    return fd
                except Exception:
                    return None
            return None
        except Exception:
            return None

    cleanup_stop_event.clear()
    if RUN_CLEANUP_WORKER:
        lock_fd = _acquire_lock(CLEANUP_LOCK_PATH)
        if lock_fd is not None:
            cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
            cleanup_thread.start()
        else:
            logger.warning(
                "[CLEANUP] Skip cleanup worker startup: lock already held by another process. "
                "Confirm another worker in the same filesystem is active."
            )
    else:
        logger.warning("[CLEANUP] RUN_CLEANUP_WORKER=0, cleanup worker disabled")
    try:
        yield
    finally:
        if cleanup_thread is not None:
            cleanup_stop_event.set()
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except Exception:
                pass
            try:
                if os.path.exists(CLEANUP_LOCK_PATH):
                    os.remove(CLEANUP_LOCK_PATH)
            except Exception:
                pass

app = FastAPI(lifespan=lifespan)

# ============================
# Security Middleware
# ============================
@app.middleware("http")
async def add_security_headers(request, call_next):
    return await security_headers(request, call_next)

# ============================
# Include Routers
# ============================
app.include_router(user.router)
app.include_router(internal.router)
