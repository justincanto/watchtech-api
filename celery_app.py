import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

# RabbitMQ connection URL
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672//")

# Create Celery app
celery_app = Celery(
    "watchtech",
    broker=RABBITMQ_URL,
    backend="rpc://",  # Use RPC for result backend (stores in RabbitMQ)
    include=["tasks.content", "tasks.source"],
)

# Celery configuration
celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    
    # Task execution settings
    task_acks_late=True,  # Acknowledge after task completion for reliability
    task_reject_on_worker_lost=True,
    
    # Worker settings
    worker_prefetch_multiplier=1,  # One task at a time per worker for heavy tasks
    
    # Result settings
    result_expires=3600,  # Results expire after 1 hour
    
    # Retry settings
    task_default_retry_delay=60,  # Retry after 60 seconds
    task_max_retries=3,
    
    # Queue configuration
    task_routes={
        "tasks.content.process_content_task": {"queue": "content"},
        "tasks.source.process_source_task": {"queue": "source"},
    },
    
    # Default queue
    task_default_queue="default",
)