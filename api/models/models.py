from sqlalchemy import (
    Column, String, Integer, Float, Text, DateTime, ForeignKey, 
    Enum, Boolean, JSON
)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from api.database import Base


class ExecutionStatus(str, enum.Enum):
    """Execution status enum."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class LogLevel(str, enum.Enum):
    """Log level enum."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Function(Base):
    """Function model - stores user-defined functions."""
    __tablename__ = "functions"
    
    id = Column(String, primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    code = Column(Text, nullable=False)
    handler = Column(String(255), default="main")  # Function entry point
    runtime = Column(String(50), default="python3.11")
    memory_limit = Column(Integer, default=128)  # MB
    timeout = Column(Integer, default=5)  # seconds
    environment_vars = Column(JSON, default={})
    requirements = Column(Text, nullable=True)  # pip dependencies
    version = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    executions = relationship("Execution", back_populates="function", cascade="all, delete-orphan")


class Execution(Base):
    """Execution model - tracks function executions."""
    __tablename__ = "executions"
    
    id = Column(String, primary_key=True)
    function_id = Column(String, ForeignKey("functions.id", ondelete="CASCADE"), nullable=False)
    status = Column(Enum(ExecutionStatus), default=ExecutionStatus.PENDING, nullable=False)
    input_data = Column(JSON, nullable=True)
    output_data = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    execution_time = Column(Float, nullable=True)  # seconds
    memory_used = Column(Integer, nullable=True)  # MB
    container_id = Column(String(255), nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    function = relationship("Function", back_populates="executions")
    logs = relationship("Log", back_populates="execution", cascade="all, delete-orphan")


class Log(Base):
    """Log model - stores execution logs."""
    __tablename__ = "logs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(String, ForeignKey("executions.id", ondelete="CASCADE"), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    level = Column(Enum(LogLevel), default=LogLevel.INFO, nullable=False)
    message = Column(Text, nullable=False)
    
    # Relationships
    execution = relationship("Execution", back_populates="logs")


class Metric(Base):
    """Metric model - stores execution metrics for monitoring."""
    __tablename__ = "metrics"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    function_id = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    execution_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    failure_count = Column(Integer, default=0)
    avg_execution_time = Column(Float, default=0.0)
    avg_memory_used = Column(Float, default=0.0)
    total_execution_time = Column(Float, default=0.0)