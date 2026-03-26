import os
import threading
import time
from collections import deque
from typing import Dict

RATE_STORE_MAX = int(os.getenv("RATE_STORE_MAX", "10000"))

_rate_hits: Dict[str, deque] = {}
_rate_last_seen: Dict[str, float] = {}
_rate_lock = threading.Lock()


def _rate_check(ip: str, limit: int, window_sec: int) -> bool:
    with _rate_lock:
        now = time.time()
        dq = _rate_hits.get(ip)
        if dq is None:
            dq = deque()
            _rate_hits[ip] = dq

        cutoff = now - window_sec
        while dq and dq[0] < cutoff:
            dq.popleft()

        if len(dq) >= limit:
            _rate_last_seen[ip] = now
            return False

        dq.append(now)
        _rate_last_seen[ip] = now

        if len(_rate_hits) > RATE_STORE_MAX:
            oldest = sorted(_rate_last_seen.items(), key=lambda kv: kv[1])[: max(1, RATE_STORE_MAX // 20)]
            for k, _ in oldest:
                _rate_hits.pop(k, None)
                _rate_last_seen.pop(k, None)

        if not dq:
            _rate_hits.pop(ip, None)
            _rate_last_seen.pop(ip, None)

        return True
