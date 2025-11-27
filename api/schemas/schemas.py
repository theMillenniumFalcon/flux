from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Any
from api.models.models import ExecutionStatus, LogLevel


# Function Schemas
class FunctionBase(BaseModel):
    """Base function schema."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    code: str = Field(..., min_length=1)
    handler: str = Field(default="main", max_length=255)
    runtime: str = Field(default="python3.11", pattern="^python3\\.(9|10|11|12)$")
    memory_limit: int = Field(default=128, ge=64, le=2048)
    timeout: int = Field(default=5, ge=1, le=300)
    environment_vars: dict[str, str] = Field(default_factory=dict)
    requirements: Optional[str] = None


class FunctionCreate(FunctionBase):
    """Schema for creating a function."""
    pass


class FunctionUpdate(BaseModel):
    """Schema for updating a function."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    code: Optional[str] = Field(None, min_length=1)
    handler: Optional[str] = Field(None, max_length=255)
    memory_limit: Optional[int] = Field(None, ge=64, le=2048)
    timeout: Optional[int] = Field(None, ge=1, le=300)
    environment_vars: Optional[dict[str, str]] = None
    requirements: Optional[str] = None
    is_active: Optional[bool] = None


class FunctionResponse(FunctionBase):
    """Schema for function response."""
    id: str
    version: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


# Execution Schemas
class ExecutionInput(BaseModel):
    """Schema for execution input."""
    input_data: Optional[dict[str, Any]] = None
    async_execution: bool = Field(default=False)


class ExecutionResponse(BaseModel):
    """Schema for execution response."""
    id: str
    function_id: str
    status: ExecutionStatus
    input_data: Optional[dict[str, Any]] = None
    output_data: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    execution_time: Optional[float] = None
    memory_used: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class ExecutionListResponse(BaseModel):
    """Schema for listing executions."""
    executions: list[ExecutionResponse]
    total: int
    page: int
    page_size: int


# Log Schemas
class LogResponse(BaseModel):
    """Schema for log response."""
    id: int
    execution_id: str
    timestamp: datetime
    level: LogLevel
    message: str
    
    class Config:
        from_attributes = True


class LogListResponse(BaseModel):
    """Schema for listing logs."""
    logs: list[LogResponse]
    total: int


# Metrics Schemas
class MetricResponse(BaseModel):
    """Schema for metrics response."""
    function_id: str
    execution_count: int
    success_count: int
    failure_count: int
    success_rate: float
    avg_execution_time: float
    avg_memory_used: float
    total_execution_time: float
    
    class Config:
        from_attributes = True


# Health Check Schema
class HealthResponse(BaseModel):
    """Schema for health check response."""
    status: str
    timestamp: datetime
    version: str
    services: dict[str, str]