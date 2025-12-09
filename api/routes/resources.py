from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.quota_manager import get_quota_manager, QuotaManager
from api.rate_limiter import get_rate_limiter, RateLimiter

router = APIRouter()


@router.get("/usage")
async def get_system_usage(
    db: AsyncSession = Depends(get_db),
    quota_manager: QuotaManager = Depends(get_quota_manager)
):
    """
    Get comprehensive system usage statistics.
    
    Returns:
        System usage statistics including executions, resources, and functions
    """
    usage_summary = await quota_manager.get_usage_summary(db)
    return usage_summary


@router.get("/rate-limit")
async def get_rate_limit_status(
    rate_limiter: RateLimiter = Depends(get_rate_limiter)
):
    """
    Get current rate limit status.
    
    Returns:
        Rate limit status and statistics
    """
    usage = await rate_limiter.get_usage_stats()
    return usage


@router.post("/rate-limit/reset")
async def reset_rate_limit(
    rate_limiter: RateLimiter = Depends(get_rate_limiter)
):
    """
    Reset rate limits (admin function).
    
    Returns:
        Success message
    """
    await rate_limiter.reset_limits()
    return {
        "status": "success",
        "message": "Rate limits reset"
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


@router.get("/limits")
async def get_system_limits():
    """
    Get information about system limits and configuration.
    
    Returns:
        System limits configuration
    """
    from api.config import get_settings
    from api.rate_limiter import get_rate_limiter
    
    settings = get_settings()
    rate_limiter = get_rate_limiter()
    
    return {
        "rate_limiting": {
            "max_requests_per_hour": rate_limiter.max_requests_per_hour,
            "max_concurrent_executions": rate_limiter.max_concurrent_executions
        },
        "execution_limits": {
            "max_execution_time": settings.max_execution_time_pro,
            "max_memory_limit": settings.max_memory_limit_pro,
            "container_pool_size": settings.container_pool_size
        },
        "retention": {
            "log_retention_days": settings.log_retention_days,
            "result_cache_ttl_seconds": settings.result_cache_ttl
        }
    }


@router.get("/metrics/system")
async def get_system_metrics():
    """
    Get overall system metrics and health.
    
    Returns:
        System metrics including pool, Docker, and execution stats
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