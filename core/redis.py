import hashlib
import json
import redis.asyncio as redis
from app.core.config import settings

redis_client: redis.Redis = None

CACHE_TTL = 60 * 60 * 24  # 24 hours in seconds


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