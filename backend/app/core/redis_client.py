"""
Redis client for token blacklisting and caching.
"""

import redis.asyncio as aioredis

from app.core.config import settings

# Lazy-initialized Redis connection
_redis_pool: aioredis.Redis | None = None

TOKEN_BLACKLIST_PREFIX = "token:blacklist:"


async def get_redis() -> aioredis.Redis:
    """Get or create async Redis connection."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_pool


async def blacklist_token(jti: str, ttl_seconds: int) -> None:
    """
    Add a token JTI to the blacklist with a TTL.
    The TTL should match the remaining time until the token expires.
    """
    r = await get_redis()
    key = f"{TOKEN_BLACKLIST_PREFIX}{jti}"
    await r.setex(key, ttl_seconds, "1")


async def is_token_blacklisted(jti: str) -> bool:
    """Check if a token JTI is blacklisted."""
    r = await get_redis()
    key = f"{TOKEN_BLACKLIST_PREFIX}{jti}"
    return await r.exists(key) > 0
