"""Unit tests for ASTValidator, including the ambiguous-dimension case found via the real
Olist integration test: the original _validate_dimension only checked "does this field exist
anywhere," which let a genuinely ambiguous dimension slip past validation and crash later,
deep inside SQLCompiler/FieldResolver, as an unhandled ValueError instead of a clean rejection.
"""
from insightflow_core.models import AnalyticalQuery, Entity, OperationType, SemanticField, SemanticModel
from insightflow_core.registry import MetricRegistry
from insightflow_core.validation import ASTValidator


def _semantic_model_with_collision() -> SemanticModel:
    # Mirrors the real Olist case: "region" mapped into both customers and sellers.
    return SemanticModel(
        entities=[
            Entity(
                name="customers",
                fields=[SemanticField(name="region", source_column="customer_state", source_file="customers", confidence=0.9)],
            ),
            Entity(
                name="sellers",
                fields=[SemanticField(name="region", source_column="seller_state", source_file="sellers", confidence=0.7)],
            ),
            Entity(
                name="orders",
                fields=[SemanticField(name="revenue", source_column="revenue", source_file="orders", confidence=1.0)],
            ),
        ],
        relationships=[],
    )


def test_validate_dimension_rejects_ambiguous_field():
    sm = _semantic_model_with_collision()
    registry = MetricRegistry()
    validator = ASTValidator(registry, sm, max_row_limit=1000)

    errors = validator._validate_dimension("region")
    assert len(errors) == 1
    assert errors[0].code == "ambiguous_dimension"
    assert "customers" in errors[0].message and "sellers" in errors[0].message


def test_validate_dimension_accepts_unambiguous_field():
    sm = _semantic_model_with_collision()
    registry = MetricRegistry()
    validator = ASTValidator(registry, sm, max_row_limit=1000)

    assert validator._validate_dimension("revenue") == []


def test_validate_rejects_query_with_ambiguous_dimension_before_compilation():
    from insightflow_core.models.registry import AggregationType
    from insightflow_core.models.registry import Measure

    sm = _semantic_model_with_collision()
    registry = MetricRegistry()
    registry.register_measure(Measure(name="revenue", entity="orders", source_field="revenue", aggregation=AggregationType.SUM))
    validator = ASTValidator(registry, sm, max_row_limit=1000)

    query = AnalyticalQuery(operation=OperationType.GROUP_BY, metric="revenue", dimension="region")
    result = validator.validate(query)
    assert not result.is_valid
    assert any(e.code == "ambiguous_dimension" for e in result.errors)


def test_validate_rejects_group_by_on_ratio_metric():
    """Found during the POC 2 closure sweep: _compile_ratio/_compile_growth/_compile_having_ratio
    never call _apply_dimension, so a GROUP_BY query against a non-BASE metric used to compile
    fine and silently ignore the dimension -- returning a one-row "grouped" result with no
    dimension column at all, not an error. This must be caught here, before compilation."""
    from insightflow_core.models.registry import AggregationType, Measure, MetricDefinition, MetricKind

    sm = _semantic_model_with_collision()
    registry = MetricRegistry()
    registry.register_measure(Measure(name="revenue", entity="orders", source_field="revenue", aggregation=AggregationType.SUM))
    registry.register_measure(Measure(name="orders", entity="orders", source_field="revenue", aggregation=AggregationType.COUNT))
    registry.register_metric(MetricDefinition(name="aov", kind=MetricKind.RATIO, numerator_measure="revenue", denominator_measure="orders"))
    validator = ASTValidator(registry, sm, max_row_limit=1000)

    query = AnalyticalQuery(operation=OperationType.GROUP_BY, metric="aov", dimension="revenue")
    result = validator.validate(query)
    assert not result.is_valid
    assert any(e.code == "group_by_unsupported_for_metric_kind" for e in result.errors)


def test_validate_accepts_group_by_on_base_measure():
    from insightflow_core.models.registry import AggregationType, Measure

    sm = _semantic_model_with_collision()
    registry = MetricRegistry()
    registry.register_measure(Measure(name="revenue", entity="orders", source_field="revenue", aggregation=AggregationType.SUM))
    validator = ASTValidator(registry, sm, max_row_limit=1000)

    query = AnalyticalQuery(operation=OperationType.GROUP_BY, metric="revenue", dimension="revenue")
    result = validator.validate(query)
    assert result.is_valid


def test_validate_rejects_time_filter_against_a_measure_whose_entity_has_no_time_field():
    # Found via POC 4's real-Olist integration test: an AGGREGATE query for a bare measure on an
    # entity with no "transaction_date" field (real Olist's "revenue", on `payments`) used to
    # compile fine as far as this validator was concerned, then crash with an unhandled KeyError
    # deep inside SQLCompiler/FieldResolver -- the same class of gap _validate_growth already
    # closed for GROWTH queries, generalized here to plain AGGREGATE/GROUP_BY + time_filter.
    from datetime import date

    from insightflow_core.models import TimeFilter
    from insightflow_core.models.registry import AggregationType, Measure

    sm = _semantic_model_with_collision()  # "orders" entity here has no transaction_date field
    registry = MetricRegistry()
    registry.register_measure(Measure(name="revenue", entity="orders", source_field="revenue", aggregation=AggregationType.SUM))
    validator = ASTValidator(registry, sm, max_row_limit=1000)

    query = AnalyticalQuery(
        operation=OperationType.AGGREGATE,
        metric="revenue",
        time_filter=TimeFilter(start_date=date(2024, 1, 1), end_date=date(2024, 12, 31)),
    )
    result = validator.validate(query)
    assert not result.is_valid
    assert any(e.code == "time_filter_entity_missing_time_field" for e in result.errors)


def test_validate_accepts_time_filter_against_a_measure_whose_entity_has_a_time_field():
    from datetime import date

    from insightflow_core.models import Entity, SemanticField, TimeFilter
    from insightflow_core.models.registry import AggregationType, Measure

    sm = SemanticModel(
        entities=[
            Entity(
                name="orders",
                fields=[
                    SemanticField(name="revenue", source_column="revenue", source_file="orders", confidence=1.0),
                    SemanticField(name="transaction_date", source_column="order_date", source_file="orders", confidence=1.0),
                ],
            )
        ],
        relationships=[],
    )
    registry = MetricRegistry()
    registry.register_measure(Measure(name="revenue", entity="orders", source_field="revenue", aggregation=AggregationType.SUM))
    validator = ASTValidator(registry, sm, max_row_limit=1000)

    query = AnalyticalQuery(
        operation=OperationType.AGGREGATE,
        metric="revenue",
        time_filter=TimeFilter(start_date=date(2024, 1, 1), end_date=date(2024, 12, 31)),
    )
    result = validator.validate(query)
    assert result.is_valid
