import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any

from celery import Task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from workers.celery_app import celery_app
from api.config import get_settings
from api.models.models import Function, Execution, ExecutionStatus, Log, LogLevel, Metric
from executor.runtime import get_runtime_engine
from executor.docker_manager import get_docker_manager

logger = logging.getLogger(__name__)
settings = get_settings()

# Create async database session for workers
worker_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
WorkerAsyncSession = async_sessionmaker(worker_engine, expire_on_commit=False)


class AsyncTask(Task):
    """Base task class that supports async operations."""
    
    def __call__(self, *args, **kwargs):
        """Override to support async task execution."""
        return asyncio.run(self.run_async(*args, **kwargs))
    
    async def run_async(self, *args, **kwargs):
        """Async task implementation to be overridden."""
        raise NotImplementedError


@celery_app.task(
    bind=True,
    name="workers.tasks.execute_function",
    base=AsyncTask,
    max_retries=0,
    time_limit=settings.celery_task_time_limit
)
async def execute_function(self, execution_id: str) -> Dict[str, Any]:
    """
    Execute a function asynchronously.
    
    Args:
        execution_id: ID of the execution to process
    
    Returns:
        Execution result
    """
    async with WorkerAsyncSession() as session:
        try:
            # Get execution with function
            result = await session.execute(
                select(Execution).where(Execution.id == execution_id)
            )
            execution = result.scalar_one_or_none()
            
            if not execution:
                logger.error(f"Execution {execution_id} not found")
                return {"error": "Execution not found"}
            
            # Get function
            func_result = await session.execute(
                select(Function).where(Function.id == execution.function_id)
            )
            function = func_result.scalar_one_or_none()
            
            if not function:
                logger.error(f"Function {execution.function_id} not found")
                execution.status = ExecutionStatus.FAILED
                execution.error_message = "Function not found"
                await session.commit()
                return {"error": "Function not found"}
            
            # Update execution status to RUNNING
            execution.status = ExecutionStatus.RUNNING
            execution.started_at = datetime.utcnow()
            await session.commit()
            
            # Execute function
            runtime_engine = get_runtime_engine()
            exec_result = await runtime_engine.execute_function(
                function=function,
                execution=execution,
                input_data=execution.input_data
            )
            
            # Update execution with results
            execution.completed_at = datetime.utcnow()
            execution.execution_time = exec_result.get("execution_time", 0)
            execution.memory_used = int(exec_result.get("memory_used", 0))
            
            if exec_result.get("success"):
                execution.status = ExecutionStatus.COMPLETED
                execution.output_data = exec_result.get("output")
            else:
                execution.status = ExecutionStatus.FAILED
                execution.error_message = exec_result.get("error", "Unknown error")
            
            # Store logs
            for log_entry in exec_result.get("logs", []):
                log = Log(
                    execution_id=execution_id,
                    level=LogLevel.INFO if log_entry.get("level") == "info" else LogLevel.ERROR,
                    message=log_entry.get("message", ""),
                    timestamp=datetime.utcnow()
                )
                session.add(log)
            
            await session.commit()
            
            logger.info(f"Execution {execution_id} completed with status {execution.status}")
            
            return {
                "execution_id": execution_id,
                "status": execution.status.value,
                "success": exec_result.get("success")
            }
            
        except asyncio.TimeoutError:
            # Handle timeout
            execution.status = ExecutionStatus.TIMEOUT
            execution.error_message = f"Execution timed out after {function.timeout} seconds"
            execution.completed_at = datetime.utcnow()
            await session.commit()
            
            logger.warning(f"Execution {execution_id} timed out")
            return {
                "execution_id": execution_id,
                "status": "timeout",
                "success": False
            }
            
        except Exception as e:
            logger.error(f"Error executing function: {e}", exc_info=True)
            
            # Update execution status to FAILED
            try:
                execution.status = ExecutionStatus.FAILED
                execution.error_message = str(e)
                execution.completed_at = datetime.utcnow()
                await session.commit()
            except Exception as update_error:
                logger.error(f"Failed to update execution status: {update_error}")
            
            return {
                "execution_id": execution_id,
                "status": "failed",
                "error": str(e),
                "success": False
            }


@celery_app.task(
    name="workers.tasks.cleanup_containers",
    base=AsyncTask
)
async def cleanup_containers() -> Dict[str, int]:
    """
    Periodic task to cleanup old Docker containers.
    
    Returns:
        Number of containers cleaned up
    """
    try:
        docker_manager = get_docker_manager()
        cleaned = await docker_manager.cleanup_old_containers(max_age_minutes=60)
        
        logger.info(f"Container cleanup completed: {cleaned} containers removed")
        return {"cleaned": cleaned}
        
    except Exception as e:
        logger.error(f"Error during container cleanup: {e}", exc_info=True)
        return {"cleaned": 0, "error": str(e)}


@celery_app.task(
    name="workers.tasks.update_metrics",
    base=AsyncTask
)
async def update_metrics() -> Dict[str, Any]:
    """
    Periodic task to update function metrics.
    
    Returns:
        Metrics update summary
    """
    async with WorkerAsyncSession() as session:
        try:
            # Get all functions
            functions_result = await session.execute(select(Function))
            functions = functions_result.scalars().all()
            
            metrics_updated = 0
            
            for function in functions:
                # Calculate metrics for last hour
                one_hour_ago = datetime.utcnow() - timedelta(hours=1)
                
                # Get executions in last hour
                executions_result = await session.execute(
                    select(Execution).where(
                        Execution.function_id == function.id,
                        Execution.created_at >= one_hour_ago
                    )
                )
                executions = executions_result.scalars().all()
                
                if not executions:
                    continue
                
                # Calculate metrics
                total_count = len(executions)
                success_count = sum(1 for e in executions if e.status == ExecutionStatus.COMPLETED)
                failure_count = sum(1 for e in executions if e.status == ExecutionStatus.FAILED)
                
                completed_executions = [e for e in executions if e.execution_time is not None]
                avg_execution_time = (
                    sum(e.execution_time for e in completed_executions) / len(completed_executions)
                    if completed_executions else 0
                )
                
                memory_executions = [e for e in executions if e.memory_used is not None]
                avg_memory_used = (
                    sum(e.memory_used for e in memory_executions) / len(memory_executions)
                    if memory_executions else 0
                )
                
                total_execution_time = sum(
                    e.execution_time for e in completed_executions
                )
                
                # Store metric
                metric = Metric(
                    function_id=function.id,
                    timestamp=datetime.utcnow(),
                    execution_count=total_count,
                    success_count=success_count,
                    failure_count=failure_count,
                    avg_execution_time=avg_execution_time,
                    avg_memory_used=avg_memory_used,
                    total_execution_time=total_execution_time
                )
                session.add(metric)
                metrics_updated += 1
            
            await session.commit()
            
            logger.info(f"Updated metrics for {metrics_updated} functions")
            return {
                "functions_processed": len(functions),
                "metrics_updated": metrics_updated
            }
            
        except Exception as e:
            logger.error(f"Error updating metrics: {e}", exc_info=True)
            return {"error": str(e)}


@celery_app.task(
    name="workers.tasks.cleanup_old_logs",
    base=AsyncTask
)
async def cleanup_old_logs() -> Dict[str, int]:
    """
    Periodic task to cleanup old execution logs.
    
    Returns:
        Number of logs deleted
    """
    async with WorkerAsyncSession() as session:
        try:
            # Delete logs older than retention period
            cutoff_date = datetime.utcnow() - timedelta(days=settings.log_retention_days)
            
            # Get old executions
            old_executions = await session.execute(
                select(Execution).where(Execution.created_at < cutoff_date)
            )
            
            deleted_count = 0
            for execution in old_executions.scalars():
                # Delete logs
                logs_result = await session.execute(
                    select(Log).where(Log.execution_id == execution.id)
                )
                logs = logs_result.scalars().all()
                
                for log in logs:
                    await session.delete(log)
                    deleted_count += 1
            
            await session.commit()
            
            logger.info(f"Cleaned up {deleted_count} old logs")
            return {"deleted": deleted_count}
            
        except Exception as e:
            logger.error(f"Error cleaning up old logs: {e}", exc_info=True)
            return {"deleted": 0, "error": str(e)}


@celery_app.task(
    name="workers.tasks.warm_container_pool",
    base=AsyncTask
)
async def warm_container_pool() -> Dict[str, Any]:
    """
    Periodic task to maintain a pool of warm containers.
    This reduces cold start time for function executions.
    
    Returns:
        Pool status
    """
    try:
        from executor.container_pool import get_container_pool
        pool = await get_container_pool()
        
        # Maintain pool
        await pool.maintain_pool()
        
        # Get stats
        stats = await pool.get_pool_stats()
        
        logger.info(f"Container pool warmed: {stats['total_containers']} containers")
        
        return {
            "status": "success",
            "stats": stats
        }
        
    except Exception as e:
        logger.error(f"Error warming container pool: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


@celery_app.task(
    name="workers.tasks.cleanup_pool_containers",
    base=AsyncTask
)
async def cleanup_pool_containers() -> Dict[str, int]:
    """
    Periodic task to cleanup old containers in the pool.
    
    Returns:
        Number of containers cleaned up
    """
    try:
        from executor.container_pool import get_container_pool
        pool = await get_container_pool()
        
        cleaned = await pool.cleanup_old_containers()
        
        logger.info(f"Pool cleanup completed: {cleaned} containers removed")
        return {"cleaned": cleaned}
        
    except Exception as e:
        logger.error(f"Error during pool cleanup: {e}", exc_info=True)
        return {"cleaned": 0, "error": str(e)}