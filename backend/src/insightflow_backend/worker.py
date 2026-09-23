"""RQ worker process entrypoint (Phase 7 M3) -- run via `python -m insightflow_backend.worker`.
See docker-compose.yml's `worker` service. Consumes whatever `jobs.enqueue_discovery_job()`
pushes onto `settings.rq_queue_name`; the job body itself lives in jobs.py, not here.

Uses `SimpleWorker` (runs each job in-process) rather than RQ's default `Worker`, which forks a
subprocess per job via `os.fork()` -- unavailable on Windows, which this project develops on.
Per-job process isolation isn't needed for a single-purpose discovery queue like this one.
"""
import logging
import time
from collections.abc import Callable

from rq.worker import SimpleWorker

from insightflow_backend.config import settings
from insightflow_backend.jobs import build_rq_connection

logger = logging.getLogger("insightflow")

_MAX_BACKOFF_SECONDS = 60
# A worker that ran at least this long before losing Redis was healthy; start the next backoff from scratch.
_HEALTHY_RUN_SECONDS = 60


def _build_worker() -> SimpleWorker:
    return SimpleWorker([settings.rq_queue_name], connection=build_rq_connection())


def run_forever(
    build_worker: Callable[[], SimpleWorker] = _build_worker,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Keep a worker running across Redis outages; return only when a stop is actually requested.

    RQ catches a Redis timeout itself, logs "Redis connection timeout, quitting...", and returns from `work()`
    exactly as if it had been asked to stop. On the Windows dev machine that happened twice in one day with
    Redis healthy moments later, and every upload after it sat at "pending" until someone noticed. A signal
    (Ctrl+C, SIGTERM) sets `_stop_requested`, which is the only return that ends this loop; a second Ctrl+C
    raises SystemExit straight through.
    """
    backoff = 1.0
    while True:
        worker = build_worker()
        started = clock()
        worker.work()
        if worker._stop_requested:
            return
        if clock() - started >= _HEALTHY_RUN_SECONDS:
            backoff = 1.0
        logger.warning("worker_lost_redis_reconnecting", extra={"retry_in_seconds": backoff})
        sleep(backoff)
        backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)


if __name__ == "__main__":
    run_forever()
