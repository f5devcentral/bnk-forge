"""Worker-side helper: fetch a Task row with a brief retry window.

API code commits the Task row before enqueuing the Celery message, but workers
can still race ahead of slow commits or replication lag. Callers should use this
helper instead of a raw query so a transient miss is retried instead of
escalating to "Task X not found".
"""

import time

from models import Task as TaskModel


def fetch_task_or_raise(db, task_id: int, *, attempts: int = 5, base_delay: float = 0.05):
    """Look up a Task row with bounded exponential backoff.

    Why: another DB connection may not yet see the row if the API request's
    commit hasn't fully propagated when the worker dequeues the message.
    """
    for attempt in range(attempts):
        task = db.query(TaskModel).filter(TaskModel.id == task_id).first()
        if task is not None:
            return task
        db.rollback()  # release the snapshot so the next read sees fresh state
        if attempt < attempts - 1:
            time.sleep(base_delay * (2 ** attempt))
    raise ValueError(f"Task {task_id} not found")
