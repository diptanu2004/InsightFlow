"""RQ worker process entrypoint (Phase 7 M3) -- run via `python -m insightflow_backend.worker`.
See docker-compose.yml's `worker` service. Consumes whatever `jobs.enqueue_discovery_job()`
pushes onto `settings.rq_queue_name`; the job body itself lives in jobs.py, not here.

Uses `SimpleWorker` (runs each job in-process) rather than RQ's default `Worker`, which forks a
subprocess per job via `os.fork()` -- unavailable on Windows, which this project develops on.
Per-job process isolation isn't needed for a single-purpose discovery queue like this one.
"""
from rq.worker import SimpleWorker

from insightflow_backend.config import settings
from insightflow_backend.jobs import build_rq_connection

if __name__ == "__main__":
    worker = SimpleWorker([settings.rq_queue_name], connection=build_rq_connection())
    worker.work()
