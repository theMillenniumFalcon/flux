import time
import logging
from typing import Optional, Dict

from fastapi import HTTPException, status
from api.redis_client import get_cache_manager, CacheManager
from api.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class RateLimiter:
    """
    Simple global rate limiter using token bucket algorithm.
    Prevents system overload without user authentication.
    """
    
    def __init__(self, cache_manager: Optional[CacheManager] = None):
        """Initialize rate limiter."""
        self.cache = cache_manager or get_cache_manager()
        # Global system limits
        self.max_requests_per_hour = 1000
        self.max_concurrent_executions = 20
    
    async def check_rate_limit(self, endpoint: str = "global") -> Dict[str, any]:
        """
        Check if request is within global rate limit.
        
        Args:
            endpoint: Endpoint identifier (for separate limits per endpoint)
        
        Returns:
            Dict with rate limit info
        
        Raises:
            HTTPException: If rate limit exceeded
        """
        # Token bucket key
        key = f"ratelimit:{endpoint}"
        
        # Get current token count and last refill time
        current_tokens = await self.cache.get(f"{key}:tokens")
        last_refill = await self.cache.get(f"{key}:last_refill")
        
        now = time.time()
        window_seconds = 3600  # 1 hour
        
        if current_tokens is None:
            # Initialize bucket
            current_tokens = self.max_requests_per_hour
            last_refill = now
        else:
            current_tokens = float(current_tokens)
            last_refill = float(last_refill)
            
            # Refill tokens based on time passed
            time_passed = now - last_refill
            tokens_to_add = (time_passed / window_seconds) * self.max_requests_per_hour
            current_tokens = min(self.max_requests_per_hour, current_tokens + tokens_to_add)
            last_refill = now
        
        # Check if we have tokens
        if current_tokens < 1:
            # Rate limit exceeded
            reset_time = last_refill + window_seconds
            retry_after = int(reset_time - now)
            
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "System rate limit exceeded. Please try again later.",
                    "limit": self.max_requests_per_hour,
                    "retry_after_seconds": retry_after
                }
            )
        
        # Consume one token
        current_tokens -= 1
        
        # Update Redis
        await self.cache.set(f"{key}:tokens", str(current_tokens), ttl=window_seconds)
        await self.cache.set(f"{key}:last_refill", str(last_refill), ttl=window_seconds)
        
        return {
            "allowed": True,
            "remaining": int(current_tokens),
            "limit": self.max_requests_per_hour,
            "reset": int(last_refill + window_seconds)
        }
    
    async def check_concurrent_limit(self) -> bool:
        """
        Check if concurrent execution limit is reached.
        
        Returns:
            True if within limit
        
        Raises:
            HTTPException: If concurrent limit exceeded
        """
        key = "concurrent:executions"
        current = await self.cache.get(key)
        current_count = int(current) if current else 0
        
        if current_count >= self.max_concurrent_executions:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "System concurrent execution limit reached. Please try again shortly.",
                    "limit": self.max_concurrent_executions,
                    "current": current_count
                }
            )
        
        return True
    
    async def increment_concurrent(self):
        """Increment concurrent execution counter."""
        key = "concurrent:executions"
        await self.cache.increment(key)
        await self.cache.expire(key, 3600)
    
    async def decrement_concurrent(self):
        """Decrement concurrent execution counter."""
        key = "concurrent:executions"
        current = await self.cache.get(key)
        
        if current and int(current) > 0:
            new_val = int(current) - 1
            if new_val <= 0:
                await self.cache.delete(key)
            else:
                await self.cache.set(key, str(new_val), ttl=3600)
    
    async def get_usage_stats(self) -> Dict:
        """Get rate limit usage statistics."""
        # Rate limit stats
        tokens_key = "ratelimit:global:tokens"
        current_tokens = await self.cache.get(tokens_key)
        current_tokens = float(current_tokens) if current_tokens else self.max_requests_per_hour
        
        # Concurrent stats
        concurrent_key = "concurrent:executions"
        concurrent_count = await self.cache.get(concurrent_key)
        concurrent_count = int(concurrent_count) if concurrent_count else 0
        
        return {
            "rate_limit": {
                "limit_per_hour": self.max_requests_per_hour,
                "remaining": int(current_tokens),
                "used": self.max_requests_per_hour - int(current_tokens)
            },
            "concurrent": {
                "limit": self.max_concurrent_executions,
                "current": concurrent_count,
                "available": self.max_concurrent_executions - concurrent_count
            }
        }
    
    async def reset_limits(self):
        """Reset all limits (admin function)."""
        keys_to_delete = [
            "ratelimit:global:tokens",
            "ratelimit:global:last_refill",
            "concurrent:executions"
        ]
        
        for key in keys_to_delete:
            await self.cache.delete(key)
        
        logger.info("Reset all rate limits")


# Singleton instance
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get or create rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter