"""Redis-backed queue for async exercise evaluation."""
from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)

QUEUE_NAME = "freelingo:eval_queue"

_redis: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
        )
    return _redis


async def enqueue_exercise(exercise_id: int, payload: dict[str, Any]) -> None:
    """Push an exercise evaluation job onto the queue."""
    r = await _get_redis()
    job = json.dumps({"exercise_id": exercise_id, **payload})
    await r.rpush(QUEUE_NAME, job)
    logger.info("Enqueued exercise %d for evaluation", exercise_id)


async def dequeue_exercise() -> dict[str, Any] | None:
    """Pop the next exercise evaluation job (blocking with short timeout)."""
    r = await _get_redis()
    _, raw = await r.blpop(QUEUE_NAME, timeout=1)
    if raw is None:
        return None
    return json.loads(raw)


async def queue_length() -> int:
    r = await _get_redis()
    return await r.llen(QUEUE_NAME)
