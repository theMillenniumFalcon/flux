from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings."""
    
    # Application
    app_name: str = "Flux"
    app_version: str = "0.1.0"
    debug: bool = True
    environment: str = "development"
    
    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    
    # Database
    database_url: str
    database_pool_size: int = 20
    database_max_overflow: int = 10
    
    # Redis
    redis_url: str
    redis_max_connections: int = 50

    # Celery
    celery_broker_url: str
    celery_result_backend: str
    celery_task_track_started: bool = True
    celery_task_time_limit: int = 300
    celery_worker_prefetch_multiplier: int = 4
    celery_worker_max_tasks_per_child: int = 1000

    # Execution Limits
    container_pool_size: int = 5

    # Storage
    log_retention_days: int = 7
    
    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()