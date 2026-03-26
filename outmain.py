"""
Legacy entrypoint disabled on purpose.

Use `cloud_public/main.py` (`uvicorn main:app`) as the only supported runtime entrypoint.
"""

raise RuntimeError(
    "cloud_public/outmain.py is archived and must not be used. "
    "Start cloud_public with `uvicorn main:app`."
)
