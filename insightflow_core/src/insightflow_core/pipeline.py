from insightflow_core.compilation import FieldResolver, SQLCompiler
from insightflow_core.execution import QueryExecutor
from insightflow_core.models import AnalyticalQuery, MetricResult, SemanticModel
from insightflow_core.registry import MetricRegistry
from insightflow_core.safety import SQLSafetyChecker
from insightflow_core.validation import ASTValidator


class AnalyticsEnginePipeline:
    """Orchestrates POC 2 end to end: validate -> compile -> check -> execute. Mirrors POC 1's
    SchemaDiscoveryPipeline — the single entry point a future FastAPI route calls directly:

        pipeline = AnalyticsEnginePipeline(registry, semantic_model, ...)
        result = pipeline.run(query)
        return result   # MetricResult is already a pydantic BaseModel

    All dependencies are injectable, same as POC 1's pipeline, so tests can swap any stage
    (e.g. a stub QueryExecutor instead of real DuckDB).
    """

    def __init__(
        self,
        registry: MetricRegistry,
        validator: ASTValidator,
        compiler: SQLCompiler,
        checker: SQLSafetyChecker,
        executor: QueryExecutor,
    ):
        self.registry = registry
        self.validator = validator
        self.compiler = compiler
        self.checker = checker
        self.executor = executor

    def run(self, query: AnalyticalQuery) -> MetricResult:
        validation = self.validator.validate(query)
        if not validation.is_valid:
            raise ValueError(f"invalid query: {[e.message for e in validation.errors]}")

        compiled = self.compiler.compile(query)

        check = self.checker.check(compiled)
        if not check.is_safe:
            raise ValueError(f"compiled SQL failed safety check: {check.violations}")

        return self.executor.execute(compiled, query.metric)


def build_pipeline(
    semantic_model: SemanticModel,
    registry: MetricRegistry,
    data_dir: str,
    *,
    duckdb_path: str = ":memory:",
    query_timeout_seconds: int = 10,
    max_row_limit: int = 1000,
) -> AnalyticsEnginePipeline:
    """Convenience wiring for the common case — construct every stage from just a
    SemanticModel + MetricRegistry + data directory, instead of a caller re-wiring five
    constructors by hand every time. Not required (each stage can be constructed and injected
    directly — that's what this package's own tests mostly do).

    `duckdb_path`/`query_timeout_seconds`/`max_row_limit` are keyword-only with the same defaults
    poc2_analytics_engine's own `insightflow.config.Settings` has always used (`:memory:`, 10s,
    1000 rows) — deliberately explicit parameters here rather than this package reaching into a
    caller's config module, so insightflow-core has no runtime dependency on any POC-specific code
    at all (earlier draft of this function did exactly that via a lazy `from insightflow.config
    import settings`; changed after the extraction because it made this package's own test suite
    unable to exercise build_pipeline() without a POC's `insightflow` package also being
    installed — the opposite of "pluggable"). A caller that wants config-driven values just passes
    them through explicitly, e.g. `build_pipeline(model, registry, data_dir,
    duckdb_path=settings.duckdb_path, query_timeout_seconds=settings.query_timeout_seconds,
    max_row_limit=settings.max_row_limit)` — see poc2_analytics_engine/scripts/run_poc2.py.
    """
    field_resolver = FieldResolver(semantic_model)
    executor = QueryExecutor(duckdb_path, query_timeout_seconds, max_row_limit)
    executor.register_sources(semantic_model, data_dir)
    return AnalyticsEnginePipeline(
        registry=registry,
        validator=ASTValidator(registry, semantic_model, max_row_limit),
        compiler=SQLCompiler(registry, field_resolver, max_row_limit),
        checker=SQLSafetyChecker(semantic_model, max_row_limit),
        executor=executor,
    )
