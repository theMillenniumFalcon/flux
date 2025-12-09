import logging
from typing import Dict, Optional
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from api.models.models import Function, Execution, ExecutionStatus
from api.redis_client import get_cache_manager, CacheManager

logger = logging.getLogger(__name__)


class QuotaManager:
    """
    Simplified quota manager for tracking system-wide usage.
    No user authentication - just tracks overall system metrics.
    """
    
    def __init__(self, cache_manager: Optional[CacheManager] = None):
        """Initialize quota manager."""
        self.cache = cache_manager or get_cache_manager()
    
    async def track_execution(
        self,
        execution_time: float,
        memory_used: int
    ):
        """
        Track execution for system metrics.
        
        Args:
            execution_time: Execution time in seconds
            memory_used: Memory used in MB
        """
        # Increment daily counter
        daily_key = f"quota:daily:{datetime.utcnow().strftime('%Y-%m-%d')}"
        await self.cache.increment(daily_key)
        await self.cache.expire(daily_key, 86400 * 2)  # Keep for 2 days
        
        # Increment monthly counter
        monthly_key = f"quota:monthly:{datetime.utcnow().strftime('%Y-%m')}"
        await self.cache.increment(monthly_key)
        await self.cache.expire(monthly_key, 86400 * 32)  # Keep for 32 days
        
        # Track resource usage
        usage_key = "quota:usage:system"
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
    
    async def get_usage_summary(self, db: AsyncSession) -> Dict:
        """
        Get comprehensive system usage summary.
        
        Args:
            db: Database session
        
        Returns:
            Usage summary dict
        """
        # Get execution counts
        daily_count = await self._get_daily_execution_count(db)
        monthly_count = await self._get_monthly_execution_count(db)
        
        # Get resource usage
        usage_key = "quota:usage:system"
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
        
        # Get function count
        func_count_result = await db.execute(select(func.count()).select_from(Function))
        function_count = func_count_result.scalar() or 0
        
        # Get execution success rate
        total_exec_result = await db.execute(select(func.count()).select_from(Execution))
        total_executions = total_exec_result.scalar() or 0
        
        success_exec_result = await db.execute(
            select(func.count()).select_from(Execution).where(
                Execution.status == ExecutionStatus.COMPLETED
            )
        )
        successful_executions = success_exec_result.scalar() or 0
        
        success_rate = (successful_executions / total_executions * 100) if total_executions > 0 else 0
        
        return {
            "executions": {
                "daily": daily_count,
                "monthly": monthly_count,
                "total": total_executions,
                "successful": successful_executions,
                "success_rate": round(success_rate, 2)
            },
            "resources": {
                "total_execution_time": round(resource_usage["total_execution_time"], 2),
                "total_memory_mb_seconds": round(resource_usage["total_memory_mb_seconds"], 2),
                "avg_execution_time": round(
                    resource_usage["total_execution_time"] / max(1, resource_usage["execution_count"]),
                    3
                )
            },
            "functions": {
                "total": function_count,
                "active": await self._get_active_function_count(db)
            }
        }
    
    async def _get_daily_execution_count(self, db: AsyncSession) -> int:
        """Get today's execution count."""
        cache_key = f"quota:daily:{datetime.utcnow().strftime('%Y-%m-%d')}"
        cached = await self.cache.get(cache_key)
        
        if cached:
            return int(cached)
        
        # Query database
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        result = await db.execute(
            select(func.count()).select_from(Execution).where(
                Execution.created_at >= today_start
            )
        )
        count = result.scalar() or 0
        
        # Cache
        await self.cache.set(cache_key, str(count), ttl=300)
        return count
    
    async def _get_monthly_execution_count(self, db: AsyncSession) -> int:
        """Get this month's execution count."""
        cache_key = f"quota:monthly:{datetime.utcnow().strftime('%Y-%m')}"
        cached = await self.cache.get(cache_key)
        
        if cached:
            return int(cached)
        
        # Query database
        month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        result = await db.execute(
            select(func.count()).select_from(Execution).where(
                Execution.created_at >= month_start
            )
        )
        count = result.scalar() or 0
        
        # Cache
        await self.cache.set(cache_key, str(count), ttl=300)
        return count
    
    async def _get_active_function_count(self, db: AsyncSession) -> int:
        """Get count of active functions."""
        result = await db.execute(
            select(func.count()).select_from(Function).where(
                Function.is_active == True
            )
        )
        return result.scalar() or 0


# Singleton instance
_quota_manager: Optional[QuotaManager] = None


def get_quota_manager() -> QuotaManager:
    """Get or create quota manager instance."""
    global _quota_manager
    if _quota_manager is None:
        _quota_manager = QuotaManager()
    return _quota_manager