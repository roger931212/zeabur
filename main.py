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
            logger.warning("[CLEANUP] Skip cleanup worker startup: lock already held by another process")
    else:
        logger.info("[CLEANUP] RUN_CLEANUP_WORKER=0, cleanup worker disabled")
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
