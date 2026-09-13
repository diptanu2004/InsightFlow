"""M3: real-data integration test against POC 1's actual (small, 8-order) Olist output --
`examples/real_olist_integration/data/` is copied byte-for-byte from
`poc3_dashboard_generation/examples/real_olist_integration/data/` (itself copied from POC 2's own
real-Olist example), same "self-contained POC, copy don't reference across folders" rule the
top-level README states.

No real Groq call here -- QuestionIntent objects are hand-built (same "hand-built spec, no
planner" milestone shape as poc3_dashboard_generation's own M2), so this exercises the
deterministic layer (TimeExpressionResolver, QueryAssembler, QuestionValidator, QuestionExecutor,
build_planner_context) against real, imperfect POC 1 output -- not the planner LLM.

`bootstrap_real_business_registry()` is duplicated (not imported) from
poc3_dashboard_generation/examples/real_olist_integration/run_real_test.py's function of the same
name -- same deliberate small duplication `registry/bootstrap.py` already has across POC 2/3/4
(business configuration for this specific dataset, not shared engine logic). Every judgment call
documented there still applies identically here: "revenue" maps to payments.payment_value (POC 1
also mapped it, at lower confidence, to order_items.freight_value -- shipping cost, not revenue);
"customers" counts POC 1's per-order surrogate customer_id, not the true customer_unique_id.
"""
import json
from datetime import date
from pathlib import Path

import pytest

from insightflow_core.compilation import FieldResolver
from insightflow_core.models import (
    AggregationType,
    AnalyticalQuery,
    GrowthSpec,
    HavingClause,
    MetricDefinition,
    MetricKind,
    OperationType,
    SemanticModel,
    TimeFilter,
)
from insightflow_core.models.registry import Measure
from insightflow_core.pipeline import build_pipeline
from insightflow_core.registry import MetricRegistry
from insightflow_core.validation import ASTValidator

from insightflow_chatbot.models.intent import QuestionIntent, QuestionOperation
from insightflow_chatbot.models.time_expression import TimeExpression
from insightflow_chatbot.services.date_bounds import infer_date_bounds
from insightflow_chatbot.services.executor import QuestionExecutor
from insightflow_chatbot.services.planner_context import build_planner_context
from insightflow_chatbot.services.query_assembler import QueryAssembler
from insightflow_chatbot.services.result_differ import ResultDiffer
from insightflow_chatbot.services.time_resolver import TimeExpressionResolver
from insightflow_chatbot.services.validator import QuestionValidator

DATA_DIR = Path(__file__).parent.parent / "examples" / "real_olist_integration" / "data"


def bootstrap_real_business_registry() -> MetricRegistry:
    registry = MetricRegistry()
    registry.register_measure(Measure(name="revenue", entity="payments", source_field="revenue", aggregation=AggregationType.SUM))
    registry.register_measure(Measure(name="orders", entity="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT))
    registry.register_measure(Measure(name="customers", entity="orders", source_field="customer_id", aggregation=AggregationType.COUNT_DISTINCT))
    registry.register_measure(
        Measure(name="orders_per_customer", entity="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT)
    )
    registry.register_metric(
        MetricDefinition(name="aov", kind=MetricKind.RATIO, numerator_measure="revenue", denominator_measure="orders", description="Average Order Value")
    )
    registry.register_metric(MetricDefinition(name="revenue_growth", kind=MetricKind.GROWTH, base_measure="revenue", description="Revenue growth"))
    registry.register_metric(MetricDefinition(name="order_growth", kind=MetricKind.GROWTH, base_measure="orders", description="Order-count growth"))
    registry.register_metric(MetricDefinition(name="customer_growth", kind=MetricKind.GROWTH, base_measure="customers", description="Customer growth"))
    registry.register_metric(
        MetricDefinition(
            name="repeat_purchase_rate",
            kind=MetricKind.HAVING_RATIO,
            numerator_measure="orders_per_customer",
            denominator_measure="customers",
            group_by_field="customer_id",
            group_by_entity="customers",
            group_by_source_column="customer_unique_id",
            having=HavingClause(field="orders_per_customer", operator=">=", value=2),
            description="Share of customers with 2 or more orders",
        )
    )
    return registry


@pytest.fixture(scope="module")
def real_semantic_model() -> SemanticModel:
    return SemanticModel(**json.loads((DATA_DIR / "semantic_model.json").read_text()))


@pytest.fixture(scope="module")
def real_registry() -> MetricRegistry:
    return bootstrap_real_business_registry()


@pytest.fixture(scope="module")
def real_engine(real_semantic_model, real_registry):
    return build_pipeline(real_semantic_model, real_registry, str(DATA_DIR / "raw"))


@pytest.fixture(scope="module")
def real_question_validator(real_semantic_model, real_registry):
    return QuestionValidator(ASTValidator(real_registry, real_semantic_model, 1000))


def test_infer_date_bounds_handles_a_real_timestamp_column_and_a_duplicate_canonical_mapping(real_semantic_model):
    # orders' "transaction_date" maps to TWO source columns here (order_purchase_timestamp,
    # confidence 0.98; order_approved_at, confidence 0.85) -- confirms the highest-confidence
    # tie-break, and that a full "YYYY-MM-DD HH:MM:SS" timestamp parses correctly.
    min_date, max_date = infer_date_bounds(real_semantic_model, str(DATA_DIR / "raw"), "orders", "transaction_date")
    assert min_date == date(2017, 4, 11)
    assert max_date == date(2018, 8, 3)


def test_aggregate_all_time_revenue_matches_hand_summed_real_payments(real_semantic_model, real_registry, real_engine):
    resolver = TimeExpressionResolver(reference_date=date(2018, 8, 3), min_date=date(2017, 4, 11))
    assembler = QueryAssembler(resolver)
    intent = QuestionIntent(answerable=True, metric_name="revenue", operation=QuestionOperation.AGGREGATE, time_expression=TimeExpression.ALL_TIME)
    resolved = assembler.assemble(intent)
    result = real_engine.run(resolved.queries[0])
    # sum(payments.payment_value) over all 8 rows in this real subset -- see docs/
    # real_world_integration_test.md-style provenance note above; matches the figure
    # poc3_dashboard_generation's own real-Olist README independently recorded.
    assert result.value == pytest.approx(729.39)


def test_revenue_growth_is_refused_because_payments_has_no_time_field(real_question_validator):
    # Real, previously-documented POC 2 limitation (poc3's real_world_integration_test.md):
    # "revenue" lives on `payments`, which has no transaction_date column of its own -- a GROWTH
    # query against it can never be validated, regardless of the requested time window. This is
    # exactly the kind of case QuestionValidator must catch cleanly rather than letting it crash
    # inside SQLCompiler.
    resolver = TimeExpressionResolver(reference_date=date(2018, 8, 3), min_date=date(2017, 4, 11))
    assembler = QueryAssembler(resolver)
    intent = QuestionIntent(
        answerable=True, metric_name="revenue_growth", operation=QuestionOperation.GROWTH, time_expression=TimeExpression.LAST_YEAR
    )
    resolved = assembler.assemble(intent)
    result = real_question_validator.validate(resolved)
    assert not result.is_valid
    assert any(e.code == "growth_entity_missing_time_field" for e in result.errors)


def test_order_growth_executes_against_real_sparse_data(real_engine):
    # orders DOES have transaction_date, so a GROWTH query against order_growth is structurally
    # valid here (unlike revenue_growth above). H1 vs. H2 2017 (2 orders each in this real
    # subset) is a real, defined growth number -- see the zero-comparison case tested separately
    # below, once a genuine MetricResult.shape gap.
    query = AnalyticalQuery(
        operation=OperationType.GROWTH,
        metric="order_growth",
        growth=GrowthSpec(
            current_period=TimeFilter(start_date=date(2017, 7, 1), end_date=date(2017, 12, 31)),
            comparison_period=TimeFilter(start_date=date(2017, 1, 1), end_date=date(2017, 6, 30)),
        ),
    )
    result = real_engine.run(query)
    # 2 orders (Oct 16, Dec 27) vs. 2 orders (Apr 11, May 14) -- flat, small-sample-noisy, but a
    # real, defined number.
    assert result.value == 0.0


def test_growth_from_a_zero_comparison_period_returns_an_undefined_scalar_not_a_crash(real_engine):
    # This is the exact scenario that originally surfaced the MetricResult.shape gap: LAST_YEAR
    # (2017, 4 orders) vs. its preceding year (2016, zero orders in this real subset) makes the
    # SQL's NULLIF(0, 0) divide-by-zero produce a legitimately-NULL scalar. Before
    # insightflow_core's MetricResult.shape fix, this raised a pydantic ValidationError
    # ("must set exactly one of value or rows") instead of returning a clean, defined-but-
    # undefined result -- see docs/class_diagram.md's "M3 real-data findings" for the fix.
    query = AnalyticalQuery(
        operation=OperationType.GROWTH,
        metric="order_growth",
        growth=GrowthSpec(
            current_period=TimeFilter(start_date=date(2017, 1, 1), end_date=date(2017, 12, 31)),
            comparison_period=TimeFilter(start_date=date(2016, 1, 1), end_date=date(2016, 12, 31)),
        ),
    )
    result = real_engine.run(query)
    assert result.shape == "scalar"
    assert result.value is None
    assert result.rows is None


def test_category_dimension_is_refused_as_ambiguous_against_real_poc1_output(real_question_validator):
    # Real POC 1 output maps "category" to BOTH products.product_category_name AND
    # category_translation.product_category_name -- genuinely ambiguous, and the exact case
    # poc3_dashboard_generation's own real-Groq run first surfaced. QuestionValidator must reject
    # it the same way ASTValidator always would for POC 3.
    resolver = TimeExpressionResolver(reference_date=date(2018, 8, 3), min_date=date(2017, 4, 11))
    assembler = QueryAssembler(resolver)
    intent = QuestionIntent(answerable=True, metric_name="revenue", operation=QuestionOperation.GROUP_BY, dimension="category")
    resolved = assembler.assemble(intent)
    result = real_question_validator.validate(resolved)
    assert not result.is_valid
    assert any(e.code == "ambiguous_dimension" for e in result.errors)


def test_planner_context_never_offers_category_or_region_as_a_dimension(real_semantic_model, real_registry):
    # The positive side of the finding above: build_planner_context's FieldResolver filter means
    # a real planner is never even offered "category"/"region" as usable dimensions in the first
    # place, so a real LLM call is never burned on a spec the validator was always going to
    # reject -- same lesson poc3_dashboard_generation's own real-Groq run learned the hard way.
    context = build_planner_context(real_semantic_model, real_registry, FieldResolver(real_semantic_model))
    assert "category" not in context.available_dimensions
    assert "region" not in context.available_dimensions


def test_available_dimensions_is_reduced_to_a_single_continuous_field_not_a_categorical_one(
    real_semantic_model, real_registry
):
    # Real finding, worth documenting rather than working around: in this real (small) Olist
    # subset, "category"/"region" are excluded for cross-entity ambiguity (see the tests above),
    # and it turns out order_id/customer_id/product_id are ALSO ambiguous -- each is a foreign key
    # repeated verbatim across multiple entities (order_id: orders, order_items, payments;
    # customer_id: orders, customers; product_id: order_items, products), so
    # FieldResolver.find_entity_for_field rejects them too, for the same reason as
    # category/region. The one field left standing, "price" (order_items only), is a continuous
    # numeric field, not a categorical dimension any real business question would GROUP BY --
    # FieldResolver's ambiguity check has no way to know that; it only answers "does this resolve
    # to exactly one entity," not "is this a sensible dimension." A real planner LLM could still
    # be offered "price" as `dimension` and produce a technically-valid but nonsensical GROUP BY
    # revenue by price. Flagged as a backlog item (a dimension-shaped-field heuristic, or
    # PlannerContext marking categorical vs. continuous fields), not fixed in this session.
    context = build_planner_context(real_semantic_model, real_registry, FieldResolver(real_semantic_model))
    assert context.available_dimensions == ["price"]
