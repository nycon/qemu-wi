"""
Celery application configuration for async task processing.
"""

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "qemu_interface",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.workers.vm_tasks",
        "app.workers.storage_tasks",
        "app.workers.backup_scheduler",
        "app.workers.cluster_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "vm.*": {"queue": "vm_operations"},
        "app.workers.vm_tasks.*": {"queue": "vm_operations"},
        "app.workers.storage_tasks.*": {"queue": "storage_operations"},
        "scheduler.*": {"queue": "vm_operations"},
    },
    beat_schedule={
        "check-backup-schedules": {
            "task": "scheduler.check_backup_schedules",
            "schedule": 60.0,  # every 60 seconds
        },
        "cleanup-old-backups": {
            "task": "scheduler.cleanup_old_backups",
            "schedule": crontab(minute=0),  # every hour at :00
        },
        "cluster-health-check": {
            "task": "cluster.health_check",
            "schedule": 120.0,  # every 2 minutes
        },
    },
)

celery_app.autodiscover_tasks(["app.workers"])
