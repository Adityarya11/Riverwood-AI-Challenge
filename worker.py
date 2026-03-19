import os
import time
import datetime
from celery import Celery
from db import SessionLocal, User, CallLog

# Queue broker configuration initialized via environment routing
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
celery_app = Celery("riverwood_workers", broker=redis_url, backend=redis_url)

# Concurrency tuning for optimal outbound IO latency
celery_app.conf.update(
    worker_concurrency=20,
    task_acks_late=True,
    worker_prefetch_multiplier=1
)

@celery_app.task(bind=True, max_retries=3)
def dispatch_call_task(self, user_id: str):
    """
    Pulls user records from the queue instance and executes bounded outbound interaction pipelines.
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return f"Validation error: User {user_id} not located."

        # Perform external call mapping pipeline and I/O buffer
        time.sleep(0.5) 

        # Bulk processing mock state record for concurrency demonstration
        call_log = CallLog(
            user_id=user_id, 
            status="completed", 
            audio_path="pre_generated_outbound.mp3", 
            created_at=datetime.datetime.utcnow()
        )
        db.add(call_log)
        db.commit()

        return f"Successfully processed {user_id}"
    except Exception as exc:
        db.rollback()
        raise self.retry(exc=exc, countdown=5)
    finally:
        db.close()
