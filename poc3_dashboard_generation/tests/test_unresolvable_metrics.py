"""A registered metric the uploaded dataset can't compute must never reach the planner, and a dataset
where nothing resolves must fail before spending an LLM call.

Found in Phase 8 M4: the Kaggle Olist files went through the real upload UI, POC 1 named each entity
after its filename, and every registry measure (pinned to `entity="orders"`) failed to resolve --
crashing inside the engine's FieldResolver as a raw 500 instead of a clean refusal.
"""
from datetime import date
from pathlib import Path

import pytest

from insightflow_core.compilation import FieldResolver, SQLCompiler
from insightflow_core.execution import QueryExecutor
from insightflow_core.pipeline import AnalyticsEnginePipeline
from insightflow_core.safety import SQLSafetyChecker
from insightflow_core.validation import ASTValidator
from insightflow_dashboard.dashboard.pipeline import DashboardGenerationPipeline
from insightflow_dashboard.dashboard.planner import DashboardPlanner
from insightflow_dashboard.dashboard.resolver import DashboardDataResolver
from insightflow_dashboard.dashboard.signal import SignalGatherer
from insightflow_dashboard.dashboard.spec import ComponentSpec, ComponentType, DashboardSpec
from insightflow_dashboard.dashboard.validator import DashboardValidator

DATA_DIR = Path(__file__).parent.parent / "data"


def _pipeline(semantic_model, registry, llm_client):
    field_resolver = FieldResolver(semantic_model)
    executor = QueryExecutor(":memory:", 10, 1000)
    # Views are named by entity but read `{source_file}.csv`, so renaming an entity below still
    # points at the real sample CSVs -- only the name the registry has to match changes.
    executor.register_sources(semantic_model, str(DATA_DIR / "raw" / "sample"))
    engine = AnalyticsEnginePipeline(
        registry=registry,
        validator=ASTValidator(registry, semantic_model, 1000),
        compiler=SQLCompiler(registry, field_resolver, 1000),
        checker=SQLSafetyChecker(semantic_model, 1000),
        executor=executor,
    )
    return DashboardGenerationPipeline(
        semantic_model=semantic_model,
        registry=registry,
        signal_gatherer=SignalGatherer(engine, semantic_model, registry, reference_date=date(2026, 3, 1)),
        planner=DashboardPlanner(llm_client, min_components=3, max_components=8),
        validator=DashboardValidator(registry, field_resolver, min_components=3, max_components=8),
        resolver=DashboardDataResolver(engine),
        field_resolver=field_resolver,
    )


def _rename_entity(semantic_model, old, new):
    for entity in semantic_model.entities:
        if entity.name == old:
            entity.name = new
    for relationship in semantic_model.relationships:
        relationship.from_field = relationship.from_field.replace(f"{old}.", f"{new}.")
        relationship.to_field = relationship.to_field.replace(f"{old}.", f"{new}.")


def test_pipeline_refuses_before_planning_when_nothing_resolves(semantic_model, registry, make_fake_llm_client):
    _rename_entity(semantic_model, "orders", "olist_orders_dataset")
    llm = make_fake_llm_client(AssertionError("the planner must not be called"))
    pipeline = _pipeline(semantic_model, registry, llm)

    with pytest.raises(ValueError, match="no registered metric can be computed from this dataset") as exc:
        pipeline.run()

    assert llm.last_prompt is None, "no LLM call should be spent on a dataset that can't compute anything"
    assert '"orders"' in str(exc.value), "the refusal should say which entity was missing"


def test_planner_is_only_offered_metrics_the_dataset_can_compute(semantic_model, registry, make_fake_llm_client):
    # Without a time field on `orders`, every GROWTH metric is uncomputable; plain measures still work.
    for entity in semantic_model.entities:
        if entity.name == "orders":
            entity.fields = [f for f in entity.fields if f.name != "transaction_date"]
    pipeline = _pipeline(semantic_model, registry, make_fake_llm_client(None))

    context = pipeline._build_planner_context(signals=[])
    offered = {m.name for m in context.available_metrics}

    assert "revenue" in offered and "aov" in offered
    assert not {name for name in offered if name.endswith("_growth")}


def test_validator_names_a_component_whose_metric_the_dataset_cannot_compute(semantic_model, registry):
    _rename_entity(semantic_model, "orders", "olist_orders_dataset")
    validator = DashboardValidator(registry, FieldResolver(semantic_model), min_components=1, max_components=8)
    spec = DashboardSpec(
        title="t",
        components=[ComponentSpec(component_id="kpi_revenue", type=ComponentType.KPI, metric_name="revenue")],
    )

    result = validator.validate(spec)

    assert not result.is_valid
    error = next(e for e in result.errors if e.code == "unresolvable_metric")
    assert error.component_id == "kpi_revenue"


def test_planner_is_only_offered_dimensions_each_metric_can_be_joined_to(semantic_model, registry, make_fake_llm_client):
    # Without orders -> products, revenue (on orders) can't reach category (on products).
    semantic_model.relationships = [r for r in semantic_model.relationships if "products" not in r.to_field]
    pipeline = _pipeline(semantic_model, registry, make_fake_llm_client(None))

    context = pipeline._build_planner_context(signals=[])

    assert "region" in context.groupable_dimensions["revenue"]  # orders -> customers still joins
    assert "category" not in context.groupable_dimensions["revenue"]


def test_validator_rejects_a_grouping_with_no_join_path(semantic_model, registry):
    semantic_model.relationships = [r for r in semantic_model.relationships if "products" not in r.to_field]
    validator = DashboardValidator(registry, FieldResolver(semantic_model), min_components=1, max_components=8)
    spec = DashboardSpec(
        title="t",
        components=[
            ComponentSpec(component_id="bar_category", type=ComponentType.BAR_CHART, metric_name="revenue", dimension="category")
        ],
    )

    result = validator.validate(spec)

    assert [(e.code, e.component_id) for e in result.errors] == [("no_join_path", "bar_category")]
