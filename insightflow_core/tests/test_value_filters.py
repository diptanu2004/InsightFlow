"""Filtering by dimension values -- AnalyticalQuery.filters (Phase 8).

Found on real Olist data: "total customers in AL, RN and CE" had no way to be computed, so chat returned every
region's count and the explanation LLM added three of them up. The fixture keeps Olist's shape: payments paid in
installments (several rows per order), one relationship from orders, two from customers -- and region values
with inconsistent case and whitespace, as real exports have.
"""
from datetime import date

import pytest
from pydantic import ValidationError as PydanticValidationError

from insightflow_core.compilation import FieldResolver, SQLCompiler
from insightflow_core.execution import QueryExecutor
from insightflow_core.models import (
    AggregationType,
    AnalyticalQuery,
    Entity,
    MetricDefinition,
    MetricKind,
    OperationType,
    Relationship,
    SemanticField,
    SemanticModel,
    TimeFilter,
    ValueFilter,
)
from insightflow_core.models.query import GrowthSpec, HavingClause
from insightflow_core.models.registry import Measure
from insightflow_core.pipeline import AnalyticsEnginePipeline
from insightflow_core.registry import MetricRegistry
from insightflow_core.safety import SQLSafetyChecker
from insightflow_core.validation import ASTValidator

ORDERS, PAYMENTS, CUSTOMERS = "orders_header", "payments", "customer_file"


def _field(name, column, file):
    return SemanticField(name=name, source_column=column, source_file=file, confidence=0.99)


def _model() -> SemanticModel:
    return SemanticModel(
        entities=[
            Entity(
                name=ORDERS,
                fields=[
                    _field("order_id", "order_id", ORDERS),
                    _field("customer_id", "customer_id", ORDERS),
                    _field("transaction_date", "purchased_at", ORDERS),
                    _field("order_status", "status", ORDERS),
                ],
            ),
            Entity(name=PAYMENTS, fields=[_field("order_id", "order_id", PAYMENTS), _field("revenue", "payment_value", PAYMENTS)]),
            Entity(
                name=CUSTOMERS,
                fields=[
                    _field("customer_id", "customer_id", CUSTOMERS),
                    _field("region", "state", CUSTOMERS),
                    _field("postcode", "zip", CUSTOMERS),
                ],
            ),
        ],
        relationships=[
            Relationship(from_field=f"{PAYMENTS}.order_id", to_field=f"{ORDERS}.order_id", confidence=1.0, direction="x"),
            Relationship(from_field=f"{ORDERS}.customer_id", to_field=f"{CUSTOMERS}.customer_id", confidence=1.0, direction="x"),
        ],
    )


def _registry() -> MetricRegistry:
    registry = MetricRegistry()
    registry.register_measure(Measure(name="revenue", entity=PAYMENTS, source_field="revenue", aggregation=AggregationType.SUM, format="money"))
    registry.register_measure(Measure(name="orders", entity=ORDERS, source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT))
    registry.register_measure(Measure(name="customers", entity=ORDERS, source_field="customer_id", aggregation=AggregationType.COUNT_DISTINCT))
    registry.register_metric(MetricDefinition(name="aov", kind=MetricKind.RATIO, numerator_measure="revenue", denominator_measure="orders"))
    registry.register_metric(MetricDefinition(name="order_growth", kind=MetricKind.GROWTH, base_measure="orders"))
    registry.register_metric(
        MetricDefinition(
            name="repeat_rate", kind=MetricKind.HAVING_RATIO, numerator_measure="orders", denominator_measure="customers",
            having=HavingClause(field="orders", operator=">=", value=2), group_by_field="customer_id",
        )
    )
    return registry


@pytest.fixture
def engine(tmp_path):
    (tmp_path / f"{CUSTOMERS}.csv").write_text("customer_id,state,zip\nC1,SP,1001\nC2,RJ,2002\nC3, sp ,1001\nC4,MG,3003\n")
    (tmp_path / f"{ORDERS}.csv").write_text(
        "order_id,customer_id,purchased_at,status\n"
        "O1,C1,2024-01-10 09:00:00,delivered\n"
        "O2,C2,2024-01-20 10:00:00,canceled\n"
        "O3,C3,2024-02-05 11:00:00,Delivered\n"
        "O4,C4,2024-02-10 12:00:00,delivered\n"
        "O5,C1,2024-02-15 13:00:00,canceled\n"
    )
    # O1 paid in two installments -- a JOIN from payments would count it twice per matching row.
    (tmp_path / f"{PAYMENTS}.csv").write_text("order_id,payment_value\nO1,100\nO1,50\nO2,30\nO3,200\nO4,70\nO5,10\n")
    model, registry = _model(), _registry()
    executor = QueryExecutor(":memory:", 10, 1000)
    executor.register_sources(model, str(tmp_path))
    return AnalyticsEnginePipeline(
        registry=registry,
        validator=ASTValidator(registry, model, 1000),
        compiler=SQLCompiler(registry, FieldResolver(model), 1000),
        checker=SQLSafetyChecker(model, 1000),
        executor=executor,
    )


def _query(metric, *filters, **kwargs):
    return AnalyticalQuery(operation=kwargs.pop("operation", OperationType.AGGREGATE), metric=metric, filters=list(filters), **kwargs)


def test_a_total_over_listed_values_is_computed_by_the_engine(engine):
    """The real question: customers in a set of regions, region one relationship away from the measure."""
    result = engine.run(_query("customers", ValueFilter(dimension="region", values=["SP", "RJ"])))
    assert result.value == 3  # C1 and C3 (SP, spelled " sp " for C3) and C2 (RJ)


def test_values_match_ignoring_case_and_surrounding_whitespace(engine):
    assert engine.run(_query("customers", ValueFilter(dimension="region", values=["sp"]))).value == 2


def test_a_filter_through_a_one_to_many_relationship_never_double_counts(engine):
    # Delivered orders: O1 (150 over two installments), O3 (200, status spelled "Delivered"), O4 (70).
    result = engine.run(_query("revenue", ValueFilter(dimension="order_status", values=["delivered"])))
    assert result.value == 420


def test_a_filter_on_the_measures_own_entity(engine):
    assert engine.run(_query("orders", ValueFilter(dimension="order_status", values=["canceled"]))).value == 2


def test_a_ratio_filters_numerator_and_denominator_alike(engine):
    result = engine.run(_query("aov", ValueFilter(dimension="order_status", values=["delivered"])))
    assert result.value == pytest.approx(420 / 3)


def test_grouped_results_keep_only_the_listed_values(engine):
    result = engine.run(
        _query("orders", ValueFilter(dimension="order_status", values=["delivered"]), operation=OperationType.GROUP_BY, dimension="region")
    )
    assert {row["region"]: row["value"] for row in result.rows} == {"SP": 1, " sp ": 1, "MG": 1}


def test_a_filter_combines_with_a_time_filter(engine):
    february = TimeFilter(start_date=date(2024, 2, 1), end_date=date(2024, 2, 29))
    result = engine.run(_query("revenue", ValueFilter(dimension="order_status", values=["delivered"]), time_filter=february))
    assert result.value == 270  # O3 + O4; O1 is January


def test_growth_compares_the_filtered_population_in_both_periods(engine):
    growth = GrowthSpec(
        current_period=TimeFilter(start_date=date(2024, 2, 1), end_date=date(2024, 2, 29)),
        comparison_period=TimeFilter(start_date=date(2024, 1, 1), end_date=date(2024, 1, 31)),
    )
    result = engine.run(
        _query("order_growth", ValueFilter(dimension="order_status", values=["delivered"]), operation=OperationType.GROWTH, growth=growth)
    )
    assert result.value == pytest.approx((2 - 1) / 1)


def test_a_numeric_column_matches_a_text_value(engine):
    assert engine.run(_query("customers", ValueFilter(dimension="postcode", values=["1001"]))).value == 2


def test_a_value_that_looks_like_sql_is_only_ever_a_parameter(engine):
    result = engine.run(_query("customers", ValueFilter(dimension="region", values=["SP' OR '1'='1"])))
    assert result.value == 0
    assert "OR '1'" not in result.metadata.sql


def test_two_filters_both_apply(engine):
    result = engine.run(
        _query(
            "orders",
            ValueFilter(dimension="region", values=["SP"]),
            ValueFilter(dimension="order_status", values=["canceled"]),
        )
    )
    assert result.value == 1  # O5: C1 (SP), canceled


def test_matching_reports_how_the_data_spells_each_value_and_which_are_missing(engine):
    matched = engine.match_filter_values(ValueFilter(dimension="region", values=["rj", "Atlantis"]))
    assert matched == {"rj": "RJ", "Atlantis": None}


def test_a_filter_two_relationships_away_is_refused_not_guessed(engine):
    # revenue lives on payments; region is on customers, two hops away (payments -> orders -> customers).
    with pytest.raises(ValueError, match="no direct relationship"):
        engine.run(_query("revenue", ValueFilter(dimension="region", values=["SP"])))


def test_an_unknown_filter_dimension_is_refused(engine):
    with pytest.raises(ValueError, match="not a known field"):
        engine.run(_query("orders", ValueFilter(dimension="planet", values=["Mars"])))


def test_filters_on_a_having_ratio_metric_are_refused_rather_than_ignored(engine):
    with pytest.raises(ValueError, match="having_ratio"):
        engine.run(_query("repeat_rate", ValueFilter(dimension="order_status", values=["delivered"])))


@pytest.mark.parametrize(
    "build",
    [
        lambda: ValueFilter(dimension="region", values=[]),
        lambda: ValueFilter(dimension="region", values=["  "]),
        lambda: ValueFilter(dimension="region", values=["x" * 201]),
        lambda: ValueFilter(dimension="region", values=[str(i) for i in range(51)]),
        lambda: AnalyticalQuery(
            operation=OperationType.AGGREGATE, metric="orders",
            filters=[ValueFilter(dimension="region", values=["SP"]), ValueFilter(dimension="region", values=["RJ"])],
        ),
    ],
    ids=["no-values", "blank", "too-long", "too-many", "same-dimension-twice"],
)
def test_malformed_filters_are_rejected_structurally(build):
    with pytest.raises(PydanticValidationError):
        build()


def test_duplicate_values_collapse_case_insensitively():
    assert ValueFilter(dimension="region", values=["SP", " sp", "RJ"]).values == ["SP", "RJ"]
