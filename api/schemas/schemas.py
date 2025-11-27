from pydantic import BaseModel
from datetime import datetime


# Health Check Schema
class HealthResponse(BaseModel):
    """Schema for health check response."""
    status: str
    timestamp: datetime
    version: str
    services: dict[str, str]