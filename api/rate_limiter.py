import time
import logging
from typing import Optional, Dict
from dataclasses import dataclass

from fastapi import HTTPException, status
from api.redis_client import get_cache_manager, CacheManager
from api.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class RateLimitConfig:
    """Rate limit configuration."""
    max_requests: int
    window_seconds: int
    max_concurrent: int


class RateLimitTier:
    """Rate limit tiers."""
    FREE = RateLimitConfig(
        max_requests=100,  # 100 executions per hour
        window_seconds=3600,
        max_concurrent=2  # 2 concurrent executions
    )
    
    PRO = RateLimitConfig(
        max_requests=1000,  # 1000 executions per hour
        window_seconds=3600,
        max_concurrent=10  # 10 concurrent executions
    )
    
    ENTERPRISE = RateLimitConfig(
        max_requests=10000,  # 10k executions per hour
        window_seconds=3600,
        max_concurrent=50  # 50 concurrent executions
    )


class RateLimiter:
    """
    Token bucket rate limiter with Redis backend.
    Supports both request rate limiting and concurrent execution limiting.
    """
    
    def __init__(self, cache_manager: Optional[CacheManager] = None):
        """Initialize rate limiter."""
        self.cache = cache_manager or get_cache_manager()
    
    async def check_rate_limit(
        self,
        identifier: str,
        tier: str = "free"
    ) -> Dict[str, any]:
        """
        Check if request is within rate limit.
        
        Args:
            identifier: User/function identifier
            tier: Rate limit tier (free/pro/enterprise)
        
        Returns:
            Dict with rate limit info
        
        Raises:
            HTTPException: If rate limit exceeded
        """
        config = self._get_tier_config(tier)
        
        # Token bucket key
        key = f"ratelimit:{identifier}"
        
        # Get current token count and last refill time
        current_tokens = await self.cache.get(f"{key}:tokens")
        last_refill = await self.cache.get(f"{key}:last_refill")
        
        now = time.time()
        
        if current_tokens is None:
            # Initialize bucket
            current_tokens = config.max_requests
            last_refill = now
        else:
            current_tokens = float(current_tokens)
            last_refill = float(last_refill)
            
            # Refill tokens based on time passed
            time_passed = now - last_refill
            tokens_to_add = (time_passed / config.window_seconds) * config.max_requests
            current_tokens = min(config.max_requests, current_tokens + tokens_to_add)
            last_refill = now
        
        # Check if we have tokens
        if current_tokens < 1:
            # Rate limit exceeded
            reset_time = last_refill + config.window_seconds
            retry_after = int(reset_time - now)
            
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Rate limit exceeded",
                    "limit": config.max_requests,
                    "window": config.window_seconds,
                    "retry_after": retry_after
                }
            )
        
        # Consume one token
        current_tokens -= 1
        
        # Update Redis
        await self.cache.set(f"{key}:tokens", str(current_tokens), ttl=config.window_seconds)
        await self.cache.set(f"{key}:last_refill", str(last_refill), ttl=config.window_seconds)
        
        return {
            "allowed": True,
            "remaining": int(current_tokens),
            "limit": config.max_requests,
            "reset": int(last_refill + config.window_seconds)
        }
    
    async def check_concurrent_limit(
        self,
        identifier: str,
        tier: str = "free"
    ) -> bool:
        """
        Check if concurrent execution limit is reached.
        
        Args:
            identifier: User/function identifier
            tier: Rate limit tier
        
        Returns:
            True if within limit
        
        Raises:
            HTTPException: If concurrent limit exceeded
        """
        config = self._get_tier_config(tier)
        
        key = f"concurrent:{identifier}"
        current = await self.cache.get(key)
        current_count = int(current) if current else 0
        
        if current_count >= config.max_concurrent:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Concurrent execution limit exceeded",
                    "limit": config.max_concurrent,
                    "current": current_count
                }
            )
        
        return True
    
    async def increment_concurrent(self, identifier: str, tier: str = "free"):
        """Increment concurrent execution counter."""
        key = f"concurrent:{identifier}"
        await self.cache.increment(key)
        await self.cache.expire(key, 3600)  # Expire after 1 hour
    
    async def decrement_concurrent(self, identifier: str):
        """Decrement concurrent execution counter."""
        key = f"concurrent:{identifier}"
        current = await self.cache.get(key)
        
        if current and int(current) > 0:
            new_val = int(current) - 1
            if new_val <= 0:
                await self.cache.delete(key)
            else:
                await self.cache.set(key, str(new_val), ttl=3600)
    
    async def get_usage_stats(self, identifier: str, tier: str = "free") -> Dict:
        """Get rate limit usage statistics."""
        config = self._get_tier_config(tier)
        
        # Rate limit stats
        tokens_key = f"ratelimit:{identifier}:tokens"
        current_tokens = await self.cache.get(tokens_key)
        current_tokens = float(current_tokens) if current_tokens else config.max_requests
        
        # Concurrent stats
        concurrent_key = f"concurrent:{identifier}"
        concurrent_count = await self.cache.get(concurrent_key)
        concurrent_count = int(concurrent_count) if concurrent_count else 0
        
        return {
            "tier": tier,
            "rate_limit": {
                "limit": config.max_requests,
                "remaining": int(current_tokens),
                "window_seconds": config.window_seconds
            },
            "concurrent": {
                "limit": config.max_concurrent,
                "current": concurrent_count
            }
        }
    
    def _get_tier_config(self, tier: str) -> RateLimitConfig:
        """Get configuration for tier."""
        tier_map = {
            "free": RateLimitTier.FREE,
            "pro": RateLimitTier.PRO,
            "enterprise": RateLimitTier.ENTERPRISE
        }
        return tier_map.get(tier.lower(), RateLimitTier.FREE)
    
    async def reset_limits(self, identifier: str):
        """Reset all limits for an identifier (admin function)."""
        keys_to_delete = [
            f"ratelimit:{identifier}:tokens",
            f"ratelimit:{identifier}:last_refill",
            f"concurrent:{identifier}"
        ]
        
        for key in keys_to_delete:
            await self.cache.delete(key)
        
        logger.info(f"Reset rate limits for {identifier}")


# Singleton instance
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get or create rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter