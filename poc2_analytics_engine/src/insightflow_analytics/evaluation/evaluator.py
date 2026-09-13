from pydantic import BaseModel

from insightflow_analytics.evaluation.fixture import MetricFixture
from insightflow_core.models import MetricResult
from insightflow_core.pipeline import AnalyticsEnginePipeline


class EvaluationReport(BaseModel):
    total: int
    passed: int
    failed: int
    failures: list[str] = []
    avg_latency_ms: float

    def summary(self) -> str:
        return (
            f"Metrics -> {self.passed}/{self.total} passed "
            f"(avg latency: {self.avg_latency_ms:.1f}ms)\n"
            + ("\n".join(f"  FAILED: {f}" for f in self.failures) if self.failures else "")
        )


class Evaluator:
    """Scores an AnalyticsEnginePipeline against a fixture list. Deliberately decoupled from the
    pipeline's own internals, same reasoning as POC 1's Evaluator — runs standalone over
    fixtures pointing at their own small datasets.
    """

    def __init__(self, engine: AnalyticsEnginePipeline):
        self.engine = engine

    def evaluate(self, fixtures: list[MetricFixture]) -> EvaluationReport:
        failures: list[str] = []
        latencies: list[float] = []
        passed = 0

        for fixture in fixtures:
            try:
                result = self.engine.run(fixture.query)
            except Exception as exc:  # a fixture that can't even run is a failure, not a crash
                failures.append(f"{fixture.name}: pipeline raised {exc!r}")
                continue

            latencies.append(result.metadata.execution_time_ms)
            if self._check_result(result, fixture):
                passed += 1
            else:
                actual = result.value if result.value is not None else result.rows
                failures.append(f"{fixture.name}: expected {fixture.expected_value!r}, got {actual!r}")

        total = len(fixtures)
        avg_latency_ms = sum(latencies) / len(latencies) if latencies else 0.0
        return EvaluationReport(
            total=total, passed=passed, failed=total - passed, failures=failures, avg_latency_ms=avg_latency_ms
        )

    def _check_result(self, actual: MetricResult, fixture: MetricFixture) -> bool:
        if actual.value is not None:
            try:
                return abs(float(actual.value) - float(fixture.expected_value)) <= fixture.tolerance
            except (TypeError, ValueError):
                return False
        return actual.rows == fixture.expected_value
