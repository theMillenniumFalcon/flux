import logging
from typing import Dict, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from api.models.models import Function, Execution
from api.redis_client import get_cache_manager, CacheManager

logger = logging.getLogger(__name__)


@dataclass
class QuotaLimits:
    """Quota limits for different tiers."""
    daily_executions: int
    monthly_executions: int
    max_execution_time: int  # seconds
    max_memory: int  # MB
    max_functions: int
    max_storage_mb: int  # For logs and results


class QuotaTier:
    """Predefined quota tiers."""
    FREE = QuotaLimits(
        daily_executions=100,
        monthly_executions=1000,
        max_execution_time=5,
        max_memory=128,
        max_functions=10,
        max_storage_mb=100
    )
    
    PRO = QuotaLimits(
        daily_executions=10000,
        monthly_executions=100000,
        max_execution_time=30,
        max_memory=512,
        max_functions=100,
        max_storage_mb=10000
    )
    
    ENTERPRISE = QuotaLimits(
        daily_executions=100000,
        monthly_executions=1000000,
        max_execution_time=300,
        max_memory=2048,
        max_functions=1000,
        max_storage_mb=100000
    )


class QuotaManager:
    """
    Manages user quotas and usage tracking.
    Enforces limits on executions, resources, and storage.
    """
    
    def __init__(self, cache_manager: Optional[CacheManager] = None):
        """Initialize quota manager."""
        self.cache = cache_manager or get_cache_manager()
    
    async def check_execution_quota(
        self,
        user_id: str,
        tier: str,
        db: AsyncSession
    ) -> Dict[str, any]:
        """
        Check if user has available execution quota.
        
        Args:
            user_id: User identifier
            tier: User tier (free/pro/enterprise)
            db: Database session
        
        Returns:
            Dict with quota info
        
        Raises:
            HTTPException: If quota exceeded
        """
        limits = self._get_tier_limits(tier)
        
        # Check daily quota
        daily_count = await self._get_daily_execution_count(user_id, db)
        if daily_count >= limits.daily_executions:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Daily execution quota exceeded",
                    "limit": limits.daily_executions,
                    "used": daily_count,
                    "reset_at": self._get_next_reset_time("daily")
                }
            )
        
        # Check monthly quota
        monthly_count = await self._get_monthly_execution_count(user_id, db)
        if monthly_count >= limits.monthly_executions:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Monthly execution quota exceeded",
                    "limit": limits.monthly_executions,
                    "used": monthly_count,
                    "reset_at": self._get_next_reset_time("monthly")
                }
            )
        
        return {
            "allowed": True,
            "daily": {
                "limit": limits.daily_executions,
                "used": daily_count,
                "remaining": limits.daily_executions - daily_count
            },
            "monthly": {
                "limit": limits.monthly_executions,
                "used": monthly_count,
                "remaining": limits.monthly_executions - monthly_count
            }
        }
    
    async def check_function_quota(
        self,
        user_id: str,
        tier: str,
        db: AsyncSession
    ) -> bool:
        """
        Check if user can create more functions.
        
        Args:
            user_id: User identifier
            tier: User tier
            db: Database session
        
        Returns:
            True if within quota
        
        Raises:
            HTTPException: If quota exceeded
        """
        limits = self._get_tier_limits(tier)
        
        # Count user's functions (cache this)
        cache_key = f"quota:functions:{user_id}"
        cached_count = await self.cache.get(cache_key)
        
        if cached_count:
            function_count = int(cached_count)
        else:
            # Query database
            result = await db.execute(
                select(func.count()).select_from(Function).where(
                    Function.id.like(f"{user_id}:%")  # Assuming function IDs include user_id
                )
            )
            function_count = result.scalar() or 0
            
            # Cache for 5 minutes
            await self.cache.set(cache_key, str(function_count), ttl=300)
        
        if function_count >= limits.max_functions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "Function quota exceeded",
                    "limit": limits.max_functions,
                    "current": function_count
                }
            )
        
        return True
    
    async def validate_resource_limits(
        self,
        memory_limit: int,
        timeout: int,
        tier: str
    ) -> bool:
        """
        Validate requested resources against tier limits.
        
        Args:
            memory_limit: Requested memory in MB
            timeout: Requested timeout in seconds
            tier: User tier
        
        Returns:
            True if within limits
        
        Raises:
            HTTPException: If limits exceeded
        """
        limits = self._get_tier_limits(tier)
        
        if memory_limit > limits.max_memory:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "Memory limit exceeds tier maximum",
                    "requested": memory_limit,
                    "max": limits.max_memory,
                    "tier": tier
                }
            )
        
        if timeout > limits.max_execution_time:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "Timeout exceeds tier maximum",
                    "requested": timeout,
                    "max": limits.max_execution_time,
                    "tier": tier
                }
            )
        
        return True
    
    async def track_execution(
        self,
        user_id: str,
        execution_time: float,
        memory_used: int
    ):
        """
        Track execution for quota and billing purposes.
        
        Args:
            user_id: User identifier
            execution_time: Execution time in seconds
            memory_used: Memory used in MB
        """
        # Increment daily counter
        daily_key = f"quota:daily:{user_id}:{datetime.utcnow().strftime('%Y-%m-%d')}"
        await self.cache.increment(daily_key)
        await self.cache.expire(daily_key, 86400 * 2)  # Keep for 2 days
        
        # Increment monthly counter
        monthly_key = f"quota:monthly:{user_id}:{datetime.utcnow().strftime('%Y-%m')}"
        await self.cache.increment(monthly_key)
        await self.cache.expire(monthly_key, 86400 * 32)  # Keep for 32 days
        
        # Track resource usage
        usage_key = f"quota:usage:{user_id}"
        current_usage = await self.cache.get_hash(usage_key)
        
        if not current_usage:
            current_usage = {
                "total_execution_time": 0,
                "total_memory_mb_seconds": 0,
                "execution_count": 0
            }
        else:
            current_usage = {
                "total_execution_time": float(current_usage.get("total_execution_time", 0)),
                "total_memory_mb_seconds": float(current_usage.get("total_memory_mb_seconds", 0)),
                "execution_count": int(current_usage.get("execution_count", 0))
            }
        
        # Update usage
        current_usage["total_execution_time"] += execution_time
        current_usage["total_memory_mb_seconds"] += memory_used * execution_time
        current_usage["execution_count"] += 1
        
        await self.cache.set_hash(usage_key, {
            k: str(v) for k, v in current_usage.items()
        })
        await self.cache.expire(usage_key, 86400 * 32)
    
    async def get_usage_summary(
        self,
        user_id: str,
        tier: str,
        db: AsyncSession
    ) -> Dict:
        """
        Get comprehensive usage summary for user.
        
        Args:
            user_id: User identifier
            tier: User tier
            db: Database session
        
        Returns:
            Usage summary dict
        """
        limits = self._get_tier_limits(tier)
        
        # Get execution counts
        daily_count = await self._get_daily_execution_count(user_id, db)
        monthly_count = await self._get_monthly_execution_count(user_id, db)
        
        # Get resource usage
        usage_key = f"quota:usage:{user_id}"
        resource_usage = await self.cache.get_hash(usage_key)
        
        if resource_usage:
            resource_usage = {
                k: float(v) for k, v in resource_usage.items()
            }
        else:
            resource_usage = {
                "total_execution_time": 0,
                "total_memory_mb_seconds": 0,
                "execution_count": 0
            }
        
        # Calculate costs (for billing)
        compute_cost = self._calculate_compute_cost(
            resource_usage["total_execution_time"],
            resource_usage["total_memory_mb_seconds"]
        )
        
        return {
            "tier": tier,
            "executions": {
                "daily": {
                    "used": daily_count,
                    "limit": limits.daily_executions,
                    "remaining": max(0, limits.daily_executions - daily_count),
                    "percentage": round((daily_count / limits.daily_executions) * 100, 2)
                },
                "monthly": {
                    "used": monthly_count,
                    "limit": limits.monthly_executions,
                    "remaining": max(0, limits.monthly_executions - monthly_count),
                    "percentage": round((monthly_count / limits.monthly_executions) * 100, 2)
                }
            },
            "resources": {
                "total_execution_time": round(resource_usage["total_execution_time"], 2),
                "total_memory_mb_seconds": round(resource_usage["total_memory_mb_seconds"], 2),
                "execution_count": int(resource_usage["execution_count"]),
                "avg_execution_time": round(
                    resource_usage["total_execution_time"] / max(1, resource_usage["execution_count"]),
                    3
                )
            },
            "limits": {
                "max_execution_time": limits.max_execution_time,
                "max_memory": limits.max_memory,
                "max_functions": limits.max_functions,
                "max_storage_mb": limits.max_storage_mb
            },
            "estimated_cost": compute_cost
        }
    
    async def _get_daily_execution_count(self, user_id: str, db: AsyncSession) -> int:
        """Get today's execution count for user."""
        cache_key = f"quota:daily:{user_id}:{datetime.utcnow().strftime('%Y-%m-%d')}"
        cached = await self.cache.get(cache_key)
        
        if cached:
            return int(cached)
        
        # Query database
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        result = await db.execute(
            select(func.count()).select_from(Execution).where(
                Execution.function_id.like(f"{user_id}:%"),
                Execution.created_at >= today_start
            )
        )
        count = result.scalar() or 0
        
        # Cache
        await self.cache.set(cache_key, str(count), ttl=300)
        return count
    
    async def _get_monthly_execution_count(self, user_id: str, db: AsyncSession) -> int:
        """Get this month's execution count for user."""
        cache_key = f"quota:monthly:{user_id}:{datetime.utcnow().strftime('%Y-%m')}"
        cached = await self.cache.get(cache_key)
        
        if cached:
            return int(cached)
        
        # Query database
        month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        result = await db.execute(
            select(func.count()).select_from(Execution).where(
                Execution.function_id.like(f"{user_id}:%"),
                Execution.created_at >= month_start
            )
        )
        count = result.scalar() or 0
        
        # Cache
        await self.cache.set(cache_key, str(count), ttl=300)
        return count
    
    def _get_tier_limits(self, tier: str) -> QuotaLimits:
        """Get limits for tier."""
        tier_map = {
            "free": QuotaTier.FREE,
            "pro": QuotaTier.PRO,
            "enterprise": QuotaTier.ENTERPRISE
        }
        return tier_map.get(tier.lower(), QuotaTier.FREE)
    
    def _get_next_reset_time(self, period: str) -> str:
        """Get next reset time for period."""
        now = datetime.utcnow()
        
        if period == "daily":
            next_reset = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        else:  # monthly
            if now.month == 12:
                next_reset = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0)
            else:
                next_reset = now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0)
        
        return next_reset.isoformat()
    
    def _calculate_compute_cost(self, execution_time: float, memory_mb_seconds: float) -> float:
        """
        Calculate estimated compute cost.
        Based on AWS Lambda-like pricing model.
        """
        # Example pricing: $0.0000166667 per GB-second
        gb_seconds = memory_mb_seconds / 1024
        cost = gb_seconds * 0.0000166667
        
        # Add request cost: $0.20 per 1M requests
        request_cost = (execution_time / 1000000) * 0.20
        
        return round(cost + request_cost, 6)


# Singleton instance
_quota_manager: Optional[QuotaManager] = None


def get_quota_manager() -> QuotaManager:
    """Get or create quota manager instance."""
    global _quota_manager
    if _quota_manager is None:
        _quota_manager = QuotaManager()
    return _quota_manager