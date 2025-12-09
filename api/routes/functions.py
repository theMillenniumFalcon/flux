from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List, Dict, Any
import uuid

from api.database import get_db
from api.models.models import Function
from api.schemas.schemas import (
    FunctionCreate, FunctionUpdate, FunctionResponse
)
from api.redis_client import get_cache_manager, CacheManager

router = APIRouter()


@router.post("", response_model=FunctionResponse, status_code=status.HTTP_201_CREATED)
async def create_function(
    function_data: FunctionCreate,
    db: AsyncSession = Depends(get_db),
    cache: CacheManager = Depends(get_cache_manager)
):
    """
    Create a new function.
    """
    # Generate unique ID
    function_id = f"func_{uuid.uuid4().hex[:12]}"
    
    # Create function
    new_function = Function(
        id=function_id,
        name=function_data.name,
        description=function_data.description,
        code=function_data.code,
        handler=function_data.handler,
        runtime=function_data.runtime,
        memory_limit=function_data.memory_limit,
        timeout=function_data.timeout,
        environment_vars=function_data.environment_vars,
        requirements=function_data.requirements,
    )
    
    db.add(new_function)
    await db.commit()
    await db.refresh(new_function)
    
    # Cache function code for faster execution
    cache_key = f"function:code:{function_id}"
    await cache.set(cache_key, function_data.code, ttl=3600)
    
    return new_function


@router.get("", response_model=List[FunctionResponse])
async def list_functions(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    active_only: bool = Query(False),
    db: AsyncSession = Depends(get_db)
):
    """
    List all functions with pagination.
    """
    query = select(Function)
    
    if active_only:
        query = query.where(Function.is_active == True)
    
    query = query.offset(skip).limit(limit).order_by(Function.created_at.desc())
    
    result = await db.execute(query)
    functions = result.scalars().all()
    
    return functions


@router.get("/{function_id}", response_model=FunctionResponse)
async def get_function(
    function_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get a specific function by ID.
    """
    result = await db.execute(
        select(Function).where(Function.id == function_id)
    )
    function = result.scalar_one_or_none()
    
    if not function:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Function with id '{function_id}' not found"
        )
    
    return function


@router.put("/{function_id}", response_model=FunctionResponse)
async def update_function(
    function_id: str,
    function_data: FunctionUpdate,
    db: AsyncSession = Depends(get_db),
    cache: CacheManager = Depends(get_cache_manager)
):
    """
    Update an existing function.
    """
    result = await db.execute(
        select(Function).where(Function.id == function_id)
    )
    function = result.scalar_one_or_none()
    
    if not function:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Function with id '{function_id}' not found"
        )
    
    # Update fields
    update_data = function_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(function, field, value)
    
    # Increment version if code changed
    if "code" in update_data:
        function.version += 1
        # Update cache
        cache_key = f"function:code:{function_id}"
        await cache.set(cache_key, update_data["code"], ttl=3600)
    
    await db.commit()
    await db.refresh(function)
    
    return function


@router.delete("/{function_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_function(
    function_id: str,
    db: AsyncSession = Depends(get_db),
    cache: CacheManager = Depends(get_cache_manager)
):
    """
    Delete a function.
    """
    result = await db.execute(
        select(Function).where(Function.id == function_id)
    )
    function = result.scalar_one_or_none()
    
    if not function:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Function with id '{function_id}' not found"
        )
    
    await db.delete(function)
    await db.commit()
    
    # Delete from cache
    cache_key = f"function:code:{function_id}"
    await cache.delete(cache_key)
    
    return None


@router.get("/{function_id}/stats")
async def get_function_stats(
    function_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get statistics for a function.
    """
    # Check if function exists
    result = await db.execute(
        select(Function).where(Function.id == function_id)
    )
    function = result.scalar_one_or_none()
    
    if not function:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Function with id '{function_id}' not found"
        )
    
    # Get execution count
    from api.models.models import Execution, ExecutionStatus
    
    total_executions = await db.execute(
        select(func.count()).select_from(Execution).where(
            Execution.function_id == function_id
        )
    )
    
    successful_executions = await db.execute(
        select(func.count()).select_from(Execution).where(
            Execution.function_id == function_id,
            Execution.status == ExecutionStatus.COMPLETED
        )
    )
    
    failed_executions = await db.execute(
        select(func.count()).select_from(Execution).where(
            Execution.function_id == function_id,
            Execution.status == ExecutionStatus.FAILED
        )
    )
    
    avg_execution_time = await db.execute(
        select(func.avg(Execution.execution_time)).where(
            Execution.function_id == function_id,
            Execution.status == ExecutionStatus.COMPLETED
        )
    )
    
    return {
        "function_id": function_id,
        "total_executions": total_executions.scalar() or 0,
        "successful_executions": successful_executions.scalar() or 0,
        "failed_executions": failed_executions.scalar() or 0,
        "avg_execution_time": round(avg_execution_time.scalar() or 0, 3)
    }


@router.post("/{function_id}/validate")
async def validate_function(
    function_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Validate function code for syntax errors.
    """
    result = await db.execute(
        select(Function).where(Function.id == function_id)
    )
    function = result.scalar_one_or_none()
    
    if not function:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Function with id '{function_id}' not found"
        )
    
    from executor.runtime import get_runtime_engine
    runtime_engine = get_runtime_engine()
    
    validation_result = await runtime_engine.validate_function_code(
        code=function.code,
        runtime=function.runtime
    )
    
    return validation_result


@router.post("/{function_id}/test")
async def test_function(
    function_id: str,
    test_input: Dict[str, Any] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Test a function with sample input without creating an execution record.
    """
    result = await db.execute(
        select(Function).where(Function.id == function_id, Function.is_active == True)
    )
    function = result.scalar_one_or_none()
    
    if not function:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Function with id '{function_id}' not found or inactive"
        )
    
    from executor.runtime import get_runtime_engine
    runtime_engine = get_runtime_engine()
    
    test_result = await runtime_engine.test_function(
        function=function,
        test_input=test_input
    )
    
    return test_result