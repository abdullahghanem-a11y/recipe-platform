import hashlib
import json
import redis.asyncio as redis
from fastapi import HTTPException
from app.core.config import settings

redis_client: redis.Redis = None

CACHE_TTL = 60 * 60 * 24  # 24 hours

# Rate limits: max requests per minute per API key
RATE_LIMITS = {
    "/ai/recognize":   30,
    "/ai/generate":    10,
    "/ai/nutrition":   30,
    "/ai/substitute":  30,
    "/ai/assist":       5,
}


async def get_redis() -> redis.Redis:
    global redis_client
    if redis_client is None:
        redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return redis_client


def make_cache_key(endpoint: str, payload: dict) -> str:
    raw = json.dumps({"endpoint": endpoint, "payload": payload}, sort_keys=True)
    return "cache:" + hashlib.sha256(raw.encode()).hexdigest()


async def get_cached(key: str) -> dict | None:
    client = await get_redis()
    value = await client.get(key)
    if value:
        return json.loads(value)
    return None


async def set_cached(key: str, value: dict) -> None:
    client = await get_redis()
    await client.setex(key, CACHE_TTL, json.dumps(value))


async def check_rate_limit(endpoint: str, api_key_id: str) -> None:
    """
    Sliding window rate limiter using Redis INCR + EXPIRE.
    Raises HTTP 429 if the API key has exceeded its limit for this endpoint.
    """
    limit = RATE_LIMITS.get(endpoint)
    if limit is None:
        return  # no limit configured for this endpoint

    client = await get_redis()

    # Key is scoped to: endpoint + api_key_id + current minute
    import time
    window = int(time.time() // 60)  # changes every 60 seconds
    rate_key = f"rate:{endpoint}:{api_key_id}:{window}"

    count = await client.incr(rate_key)

    # Set expiry on first request in this window (60s + small buffer)
    if count == 1:
        await client.expire(rate_key, 70)

    if count > limit:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "message": f"Too many requests. Limit is {limit} requests per minute for this endpoint.",
                "limit": limit,
                "retry_after_seconds": 60 - (int(time.time()) % 60),
            },
        )