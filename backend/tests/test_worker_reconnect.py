"""worker.run_forever -- the discovery worker survives Redis outages instead of exiting.

Found twice in one day on the Windows dev machine: RQ logged "Redis connection timeout, quitting...", the worker
process exited cleanly, and uploads sat at "pending" with Redis healthy again moments later.
"""
from insightflow_backend.worker import run_forever


class _FakeWorker:
    def __init__(self, stop_requested: bool, run_seconds: float, clock: list[float]):
        self._stop_requested = stop_requested
        self.run_seconds = run_seconds
        self.clock = clock

    def work(self):
        self.clock[0] += self.run_seconds


def _run(outcomes):
    """outcomes: (stop_requested, seconds the worker ran) per worker built, in order."""
    now = [0.0]
    built, sleeps = [], []
    remaining = list(outcomes)

    def build():
        stop, seconds = remaining.pop(0)
        worker = _FakeWorker(stop, seconds, now)
        built.append(worker)
        return worker

    run_forever(build_worker=build, sleep=sleeps.append, clock=lambda: now[0])
    return built, sleeps


def test_a_lost_redis_connection_rebuilds_the_worker_instead_of_exiting():
    built, sleeps = _run([(False, 5), (False, 5), (True, 5)])
    assert len(built) == 3
    assert sleeps == [1.0, 2.0]  # backs off between reconnect attempts


def test_a_requested_stop_ends_the_loop_without_reconnecting():
    built, sleeps = _run([(True, 100)])
    assert len(built) == 1 and sleeps == []


def test_backoff_is_capped_and_resets_after_a_healthy_run():
    outages = [(False, 1)] * 8 + [(False, 3600), (False, 1), (True, 1)]
    _, sleeps = _run(outages)
    assert sleeps[:8] == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]
    assert sleeps[8] == 1.0  # the worker ran for an hour before this outage: start over
