from insightflow_core.models.registry import Measure, MetricDefinition


class MetricRegistry:
    """The two-tier registry: Tier 1 measures, Tier 2 named metrics. See hld.md's
    "Metric Registry — two-tier structure" section. Pure storage/lookup — no SQL, no Semantic
    Model awareness; that's ASTValidator's and SQLCompiler's job, both of which take a
    MetricRegistry as a dependency.
    """

    def __init__(self) -> None:
        self.measures: dict[str, Measure] = {}
        self.metrics: dict[str, MetricDefinition] = {}

    def register_measure(self, measure: Measure) -> None:
        if measure.name in self.measures:
            raise ValueError(f"measure {measure.name!r} is already registered")
        self.measures[measure.name] = measure

    def register_metric(self, metric: MetricDefinition) -> None:
        if metric.name in self.metrics:
            raise ValueError(f"metric {metric.name!r} is already registered")
        self.metrics[metric.name] = metric

    def resolve(self, name: str) -> Measure | MetricDefinition:
        if name in self.metrics:
            return self.metrics[name]
        if name in self.measures:
            return self.measures[name]
        raise KeyError(f"{name!r} is not a registered measure or metric")

    def is_registered(self, name: str) -> bool:
        return name in self.metrics or name in self.measures
