from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.quota_manager import get_quota_manager, QuotaManager
from api.rate_limiter import get_rate_limiter, RateLimiter

router = APIRouter()


@router.get("/quota/{user_id}")
async def get_quota_usage(
    user_id: str,
    tier: str = Query("free", regex="^(free|pro|enterprise)$"),
    db: AsyncSession = Depends(get_db),
    quota_manager: QuotaManager = Depends(get_quota_manager)
):
    """
    Get comprehensive quota usage for a user.
    
    Args:
        user_id: User identifier
        tier: User tier (free/pro/enterprise)
    
    Returns:
        Detailed quota usage statistics
    """
    usage_summary = await quota_manager.get_usage_summary(user_id, tier, db)
    return usage_summary


@router.get("/rate-limit/{identifier}")
async def get_rate_limit_status(
    identifier: str,
    tier: str = Query("free", regex="^(free|pro|enterprise)$"),
    rate_limiter: RateLimiter = Depends(get_rate_limiter)
):
    """
    Get rate limit status for an identifier.
    
    Args:
        identifier: User/function identifier
        tier: Rate limit tier
    
    Returns:
        Rate limit status and statistics
    """
    usage = await rate_limiter.get_usage_stats(identifier, tier)
    return usage


@router.post("/rate-limit/{identifier}/reset")
async def reset_rate_limit(
    identifier: str,
    rate_limiter: RateLimiter = Depends(get_rate_limiter)
):
    """
    Reset rate limits for an identifier (admin function).
    
    Args:
        identifier: User/function identifier
    
    Returns:
        Success message
    """
    await rate_limiter.reset_limits(identifier)
    return {
        "status": "success",
        "message": f"Rate limits reset for {identifier}"
    }


@router.get("/pool/stats")
async def get_pool_stats():
    """
    Get container pool statistics.
    
    Returns:
        Container pool statistics
    """
    from executor.container_pool import get_container_pool
    
    try:
        pool = await get_container_pool()
        stats = await pool.get_pool_stats()
        return {
            "status": "healthy",
            "pool": stats
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }


@router.post("/pool/maintain")
async def maintain_pool():
    """
    Manually trigger pool maintenance (admin function).
    
    Returns:
        Maintenance result
    """
    from executor.container_pool import get_container_pool
    
    try:
        pool = await get_container_pool()
        await pool.maintain_pool()
        stats = await pool.get_pool_stats()
        
        return {
            "status": "success",
            "message": "Pool maintenance completed",
            "stats": stats
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }


@router.post("/pool/cleanup")
async def cleanup_pool():
    """
    Manually trigger pool cleanup (admin function).
    
    Returns:
        Cleanup result
    """
    from executor.container_pool import get_container_pool
    
    try:
        pool = await get_container_pool()
        cleaned = await pool.cleanup_old_containers()
        
        return {
            "status": "success",
            "cleaned": cleaned
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }


@router.get("/tiers")
async def get_tier_information():
    """
    Get information about available tiers and their limits.
    
    Returns:
        Tier information
    """
    from api.quota_manager import QuotaTier
    from api.rate_limiter import RateLimitTier
    
    return {
        "tiers": {
            "free": {
                "quota": {
                    "daily_executions": QuotaTier.FREE.daily_executions,
                    "monthly_executions": QuotaTier.FREE.monthly_executions,
                    "max_execution_time": QuotaTier.FREE.max_execution_time,
                    "max_memory": QuotaTier.FREE.max_memory,
                    "max_functions": QuotaTier.FREE.max_functions,
                    "max_storage_mb": QuotaTier.FREE.max_storage_mb
                },
                "rate_limit": {
                    "max_requests_per_hour": RateLimitTier.FREE.max_requests,
                    "max_concurrent": RateLimitTier.FREE.max_concurrent
                }
            },
            "pro": {
                "quota": {
                    "daily_executions": QuotaTier.PRO.daily_executions,
                    "monthly_executions": QuotaTier.PRO.monthly_executions,
                    "max_execution_time": QuotaTier.PRO.max_execution_time,
                    "max_memory": QuotaTier.PRO.max_memory,
                    "max_functions": QuotaTier.PRO.max_functions,
                    "max_storage_mb": QuotaTier.PRO.max_storage_mb
                },
                "rate_limit": {
                    "max_requests_per_hour": RateLimitTier.PRO.max_requests,
                    "max_concurrent": RateLimitTier.PRO.max_concurrent
                }
            },
            "enterprise": {
                "quota": {
                    "daily_executions": QuotaTier.ENTERPRISE.daily_executions,
                    "monthly_executions": QuotaTier.ENTERPRISE.monthly_executions,
                    "max_execution_time": QuotaTier.ENTERPRISE.max_execution_time,
                    "max_memory": QuotaTier.ENTERPRISE.max_memory,
                    "max_functions": QuotaTier.ENTERPRISE.max_functions,
                    "max_storage_mb": QuotaTier.ENTERPRISE.max_storage_mb
                },
                "rate_limit": {
                    "max_requests_per_hour": RateLimitTier.ENTERPRISE.max_requests,
                    "max_concurrent": RateLimitTier.ENTERPRISE.max_concurrent
                }
            }
        }
    }


@router.get("/metrics/system")
async def get_system_metrics():
    """
    Get overall system metrics and health.
    
    Returns:
        System metrics
    """
    from executor.container_pool import get_container_pool
    import docker
    
    try:
        # Get pool stats
        pool = await get_container_pool()
        pool_stats = await pool.get_pool_stats()
        
        # Get Docker stats
        docker_client = docker.from_env()
        all_containers = docker_client.containers.list(all=True)
        flux_containers = [c for c in all_containers if 'flux-runtime' in str(c.image.tags)]
        
        return {
            "status": "healthy",
            "pool": pool_stats,
            "docker": {
                "total_containers": len(all_containers),
                "flux_containers": len(flux_containers),
                "running": len([c for c in flux_containers if c.status == 'running'])
            }
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }