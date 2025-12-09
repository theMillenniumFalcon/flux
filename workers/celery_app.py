from celery import Celery
from api.config import get_settings

settings = get_settings()

# Create Celery app
celery_app = Celery(
    "flux",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["workers.tasks"]
)

# Configure Celery
celery_app.conf.update(
    task_track_started=settings.celery_task_track_started,
    task_time_limit=settings.celery_task_time_limit,
    worker_prefetch_multiplier=settings.celery_worker_prefetch_multiplier,
    worker_max_tasks_per_child=settings.celery_worker_max_tasks_per_child,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)

# Periodic tasks (for container cleanup, metrics, etc.)
celery_app.conf.beat_schedule = {
    "cleanup-old-containers": {
        "task": "workers.tasks.cleanup_containers",
        "schedule": 300.0,  # Every 5 minutes
    },
    "cleanup-pool-containers": {
        "task": "workers.tasks.cleanup_pool_containers",
        "schedule": 600.0,  # Every 10 minutes
    },
    "warm-container-pool": {
        "task": "workers.tasks.warm_container_pool",
        "schedule": 180.0,  # Every 3 minutes
    },
    "update-metrics": {
        "task": "workers.tasks.update_metrics",
        "schedule": 60.0,  # Every minute
    },
    "cleanup-old-logs": {
        "task": "workers.tasks.cleanup_old_logs",
        "schedule": 3600.0,  # Every hour
    },
}