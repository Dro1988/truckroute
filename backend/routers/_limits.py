"""Tiny in-memory per-IP rate limiter for expensive endpoints."""
import time
from collections import defaultdict

from fastapi import HTTPException, Request

_buckets: dict[tuple[str, str], list[float]] = defaultdict(list)


def rate_limit(request: Request, key: str, per_minute: int) -> None:
    now = time.time()
    bucket = _buckets[(key, request.client.host if request.client else "unknown")]
    bucket[:] = [t for t in bucket if now - t < 60]
    if len(bucket) >= per_minute:
        raise HTTPException(status_code=429, detail="Too many requests. Slow down a little.")
    bucket.append(now)
