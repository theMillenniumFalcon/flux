import redis.asyncio as redis
from api.config import get_settings

settings = get_settings()

# Create async Redis client
redis_client = redis.from_url(
    settings.redis_url,
    encoding="utf-8",
    decode_responses=True,
    max_connections=settings.redis_max_connections,
)


async def get_redis():
    """Dependency for getting Redis client."""
    return redis_client


async def close_redis():
    """Close Redis connection."""
    await redis_client.close()


class CacheManager:
    """Redis cache manager for common operations."""
    
    def __init__(self, client: redis.Redis):
        self.client = client
    
    async def get(self, key: str) -> str | None:
        """Get value from cache."""
        return await self.client.get(key)
    
    async def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        """Set value in cache with optional TTL."""
        return await self.client.set(key, value, ex=ttl)
    
    async def delete(self, key: str) -> int:
        """Delete key from cache."""
        return await self.client.delete(key)
    
    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        return await self.client.exists(key) > 0
    
    async def increment(self, key: str, amount: int = 1) -> int:
        """Increment counter."""
        return await self.client.incrby(key, amount)
    
    async def expire(self, key: str, ttl: int) -> bool:
        """Set expiration on key."""
        return await self.client.expire(key, ttl)
    
    async def get_many(self, keys: list[str]) -> list[str | None]:
        """Get multiple values."""
        return await self.client.mget(keys)
    
    async def set_hash(self, key: str, mapping: dict) -> int:
        """Set hash fields."""
        return await self.client.hset(key, mapping=mapping)
    
    async def get_hash(self, key: str) -> dict:
        """Get all hash fields."""
        return await self.client.hgetall(key)
    
    async def get_hash_field(self, key: str, field: str) -> str | None:
        """Get single hash field."""
        return await self.client.hget(key, field)


def get_cache_manager() -> CacheManager:
    """Get cache manager instance."""
    return CacheManager(redis_client)