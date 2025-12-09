from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid

from api.database import get_db
from api.models.models import Execution, Function, Log
from api.schemas.schemas import (
    ExecutionInput, ExecutionResponse, ExecutionListResponse,
    LogListResponse
)

router = APIRouter()


@router.post("/{function_id}/execute", response_model=ExecutionResponse)
async def execute_function(
    function_id: str,
    execution_input: ExecutionInput,
    db: AsyncSession = Depends(get_db)
):
    """
    Execute a function synchronously or asynchronously.
    """
    # Check if function exists
    result = await db.execute(
        select(Function).where(Function.id == function_id, Function.is_active == True)
    )
    function = result.scalar_one_or_none()
    
    if not function:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Function with id '{function_id}' not found or inactive"
        )
    
    # Create execution record
    execution_id = f"exec_{uuid.uuid4().hex[:12]}"
    
    from api.models.models import ExecutionStatus
    
    execution = Execution(
        id=execution_id,
        function_id=function_id,
        status=ExecutionStatus.PENDING,
        input_data=execution_input.input_data
    )
    
    db.add(execution)
    await db.commit()
    await db.refresh(execution)
    
    # Import Celery task
    from workers.tasks import execute_function as execute_function_task
    
    # Trigger async execution via Celery
    execute_function_task.delay(execution_id)
    
    if execution_input.async_execution:
        # Return immediately with pending status
        return execution
    else:
        # Wait for execution to complete (with timeout)
        import asyncio
        max_wait = min(function.timeout + 5, 30)  # Max 30 seconds wait
        
        for _ in range(max_wait):
            await asyncio.sleep(1)
            await db.refresh(execution)
            
            if execution.status not in [ExecutionStatus.PENDING, ExecutionStatus.RUNNING]:
                break
        
        return execution


@router.get("/{execution_id}", response_model=ExecutionResponse)
async def get_execution(
    execution_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get execution details by ID.
    """
    result = await db.execute(
        select(Execution).where(Execution.id == execution_id)
    )
    execution = result.scalar_one_or_none()
    
    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution with id '{execution_id}' not found"
        )
    
    return execution


@router.get("", response_model=ExecutionListResponse)
async def list_executions(
    function_id: str = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """
    List executions with optional filtering by function_id.
    """
    query = select(Execution)
    
    if function_id:
        query = query.where(Execution.function_id == function_id)
    
    # Get total count
    from sqlalchemy import func
    count_query = select(func.count()).select_from(Execution)
    if function_id:
        count_query = count_query.where(Execution.function_id == function_id)
    
    total_result = await db.execute(count_query)
    total = total_result.scalar()
    
    # Get paginated results
    query = query.offset(skip).limit(limit).order_by(Execution.created_at.desc())
    result = await db.execute(query)
    executions = result.scalars().all()
    
    return ExecutionListResponse(
        executions=executions,
        total=total,
        page=skip // limit + 1,
        page_size=limit
    )


@router.get("/{execution_id}/logs", response_model=LogListResponse)
async def get_execution_logs(
    execution_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get logs for a specific execution.
    """
    # Check if execution exists
    result = await db.execute(
        select(Execution).where(Execution.id == execution_id)
    )
    execution = result.scalar_one_or_none()
    
    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution with id '{execution_id}' not found"
        )
    
    # Get logs
    logs_result = await db.execute(
        select(Log)
        .where(Log.execution_id == execution_id)
        .order_by(Log.timestamp.asc())
    )
    logs = logs_result.scalars().all()
    
    return LogListResponse(
        logs=logs,
        total=len(logs)
    )


@router.delete("/{execution_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_execution(
    execution_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Cancel a running execution.
    """
    result = await db.execute(
        select(Execution).where(Execution.id == execution_id)
    )
    execution = result.scalar_one_or_none()
    
    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution with id '{execution_id}' not found"
        )
    
    from api.models.models import ExecutionStatus
    
    if execution.status not in [ExecutionStatus.PENDING, ExecutionStatus.RUNNING]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel execution with status '{execution.status}'"
        )
    
    # TODO: Actually cancel the running container/task
    execution.status = ExecutionStatus.CANCELLED
    await db.commit()
    
    return None