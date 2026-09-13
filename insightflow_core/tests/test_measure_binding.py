"""Measures bind to entities per dataset, by semantic field -- see compilation/measure_binding.py.

The entity names below follow what POC 1 really produces for the Kaggle Olist upload (source filename
stems). The registry deliberately pins nothing, like the backend's production registry.
"""
import pytest

from insightflow_core.compilation import FieldResolver, SQLCompiler
from insightflow_core.compilation.measure_binding import UnresolvableMeasure, bind_measure, bind_metric
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
)
from insightflow_core.models.registry import Measure
from insightflow_core.pipeline import AnalyticsEnginePipeline
from insightflow_core.registry import MetricRegistry
from insightflow_core.safety import SQLSafetyChecker
from insightflow_core.validation import ASTValidator
from insightflow_core.validation.metric_resolvability import group_by_problem, resolvable_metric_names

PAYMENTS = "olist_order_payments_dataset"
ITEMS = "olist_order_items_dataset"
PRODUCTS = "olist_products_dataset"
CUSTOMERS = "olist_customers_dataset"


def _field(name, column, file):
    return SemanticField(name=name, source_column=column, source_file=file, confidence=0.99)


def _olist_model() -> SemanticModel:
    return SemanticModel(
        entities=[
            Entity(name=PAYMENTS, fields=[_field("order_id", "order_id", PAYMENTS), _field("revenue", "payment_value", PAYMENTS)]),
            Entity(
                name=ITEMS,
                fields=[_field("order_id", "order_id", ITEMS), _field("product_id", "product_id", ITEMS), _field("price", "price", ITEMS)],
            ),
            Entity(name=PRODUCTS, fields=[_field("product_id", "product_id", PRODUCTS), _field("category", "product_category_name", PRODUCTS)]),
            Entity(name=CUSTOMERS, fields=[_field("customer_id", "customer_id", CUSTOMERS), _field("region", "customer_state", CUSTOMERS)]),
        ],
        relationships=[
            Relationship(from_field=f"{ITEMS}.order_id", to_field=f"{PAYMENTS}.order_id", confidence=1.0, direction="x"),
            Relationship(from_field=f"{ITEMS}.product_id", to_field=f"{PRODUCTS}.product_id", confidence=1.0, direction="x"),
        ],
    )


def _unpinned_registry() -> MetricRegistry:
    registry = MetricRegistry()
    registry.register_measure(Measure(name="revenue", source_field="revenue", aggregation=AggregationType.SUM))
    registry.register_measure(Measure(name="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT))
    registry.register_measure(Measure(name="customers", source_field="customer_id", aggregation=AggregationType.COUNT_DISTINCT))
    registry.register_measure(Measure(name="price", source_field="price", aggregation=AggregationType.SUM))
    registry.register_metric(
        MetricDefinition(name="aov", kind=MetricKind.RATIO, numerator_measure="revenue", denominator_measure="orders")
    )
    return registry


def test_a_field_on_exactly_one_entity_binds_there_whatever_the_file_was_called():
    model = _olist_model()
    measure = Measure(name="revenue", source_field="revenue", aggregation=AggregationType.SUM)
    assert bind_measure(measure, model).entity == PAYMENTS


def test_a_field_on_no_entity_is_refused():
    measure = Measure(name="margin", source_field="margin", aggregation=AggregationType.SUM)
    with pytest.raises(UnresolvableMeasure, match="no entity in this dataset has"):
        bind_measure(measure, _olist_model())


def test_an_ambiguous_field_on_its_own_is_refused_naming_the_candidates():
    # order_id is on both payments and order_items; with nothing to co-locate with, never guess.
    measure = Measure(name="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT)
    with pytest.raises(UnresolvableMeasure) as exc:
        bind_measure(measure, _olist_model())
    assert PAYMENTS in str(exc.value) and ITEMS in str(exc.value)


def test_a_pin_is_honoured_and_a_missing_pin_is_not_silently_ignored():
    model = _olist_model()
    pinned = Measure(name="orders", entity=ITEMS, source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT)
    assert bind_measure(pinned, model).entity == ITEMS

    stale = Measure(name="orders", entity="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT)
    with pytest.raises(UnresolvableMeasure, match='entity "orders", which this dataset does not have'):
        bind_measure(stale, model)


def test_a_ratio_co_locates_an_ambiguous_measure_with_its_sibling():
    registry = _unpinned_registry()
    bound = bind_metric(registry.resolve("aov"), registry, _olist_model()).measures
    # revenue only exists on payments, so order_id -- ambiguous alone -- is counted there too.
    assert bound["numerator"].entity == PAYMENTS
    assert bound["denominator"].entity == PAYMENTS


def test_resolvability_reflects_binding_on_a_real_shaped_upload():
    names = resolvable_metric_names(_unpinned_registry(), _olist_model())
    assert names == {"revenue", "customers", "price", "aov"}  # "orders" alone stays ambiguous


def test_grouping_needs_a_single_hop_join_between_measure_and_dimension():
    registry = _unpinned_registry()
    model = _olist_model()
    # price (order_items) -> category (products): one hop.
    assert group_by_problem("price", "category", registry, model) is None
    # revenue (payments) -> category (products): payments -> order_items -> products, two hops.
    problem = group_by_problem("revenue", "category", registry, model)
    assert problem is not None and "single-hop" in problem


def test_ast_validator_rejects_a_two_hop_grouping_before_compilation():
    registry = _unpinned_registry()
    validator = ASTValidator(registry, _olist_model(), max_row_limit=1000)
    result = validator.validate(AnalyticalQuery(operation=OperationType.GROUP_BY, metric="revenue", dimension="category"))
    assert [e.code for e in result.errors] == ["no_join_path"]


def test_co_located_aov_computes_the_right_number_end_to_end(tmp_path):
    # payments: order A pays 100 + 50 (two installments), order B pays 30 -> revenue 180 over two
    # distinct orders. order_items also carries order C, which has no payment row at all -- a real
    # Olist shape (an order with line items and no payment). So the answer discriminates where the
    # denominator bound: co-located on payments AOV = 180 / 2 = 90; bound to order_items it would be
    # 180 / 3 = 60, dividing revenue by a population that revenue was never measured over.
    (tmp_path / f"{PAYMENTS}.csv").write_text("order_id,payment_value\nA,100\nA,50\nB,30\n")
    (tmp_path / f"{ITEMS}.csv").write_text("order_id,product_id,price\nA,p1,60\nA,p2,60\nA,p1,30\nB,p2,30\nC,p1,10\n")
    (tmp_path / f"{PRODUCTS}.csv").write_text("product_id,product_category_name\np1,toys\np2,books\n")
    (tmp_path / f"{CUSTOMERS}.csv").write_text("customer_id,customer_state\nc1,SP\n")

    registry = _unpinned_registry()
    model = _olist_model()
    executor = QueryExecutor(":memory:", 10, 1000)
    executor.register_sources(model, str(tmp_path))
    engine = AnalyticsEnginePipeline(
        registry=registry,
        validator=ASTValidator(registry, model, 1000),
        compiler=SQLCompiler(registry, FieldResolver(model), 1000),
        checker=SQLSafetyChecker(model, 1000),
        executor=executor,
    )

    assert engine.run(AnalyticalQuery(operation=OperationType.AGGREGATE, metric="revenue")).value == 180
    assert engine.run(AnalyticalQuery(operation=OperationType.AGGREGATE, metric="aov")).value == 90

    by_category = engine.run(AnalyticalQuery(operation=OperationType.GROUP_BY, metric="price", dimension="category"))
    assert {row["category"]: row["value"] for row in by_category.rows} == {"toys": 100, "books": 90}


ORDERS = "olist_orders_dataset"


def _full_olist_model() -> SemanticModel:
    """The shape a full upload has once the orders header is included: order_id is on four
    entities and customer_id on two -- every key count is ambiguous without a population hint."""
    model = _olist_model()
    model.entities.append(
        Entity(
            name=ORDERS,
            fields=[
                _field("order_id", "order_id", ORDERS),
                _field("customer_id", "customer_id", ORDERS),
                _field("transaction_date", "order_purchase_timestamp", ORDERS),
            ],
        )
    )
    return model


def test_a_population_hint_settles_a_key_count_on_a_normalized_schema():
    model = _full_olist_model()
    customers = Measure(name="customers", source_field="customer_id", aggregation=AggregationType.COUNT_DISTINCT, colocate_with_field="order_id")
    orders = Measure(name="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT, colocate_with_field="customer_id")

    # customers who ordered, not every customer on file
    assert bind_measure(customers, model).entity == ORDERS
    # the order header, not line items or payments
    assert bind_measure(orders, model).entity == ORDERS


def test_a_hint_never_makes_a_field_on_one_entity_unresolvable():
    # The 4-file upload has no orders header: customer_id exists only on customers.
    customers = Measure(name="customers", source_field="customer_id", aggregation=AggregationType.COUNT_DISTINCT, colocate_with_field="order_id")
    assert bind_measure(customers, _olist_model()).entity == CUSTOMERS


def test_a_hint_no_candidate_carries_leaves_the_measure_refused():
    # order_id is on payments and order_items; neither carries customer_id.
    orders = Measure(name="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT, colocate_with_field="customer_id")
    with pytest.raises(UnresolvableMeasure, match='population hint "customer_id" does not narrow'):
        bind_measure(orders, _olist_model())


def test_hinted_ratio_sides_may_bind_to_different_entities():
    registry = MetricRegistry()
    registry.register_measure(Measure(name="revenue", source_field="revenue", aggregation=AggregationType.SUM))
    registry.register_measure(
        Measure(name="orders", source_field="order_id", aggregation=AggregationType.COUNT_DISTINCT, colocate_with_field="customer_id")
    )
    registry.register_metric(
        MetricDefinition(name="aov", kind=MetricKind.RATIO, numerator_measure="revenue", denominator_measure="orders")
    )

    bound = bind_metric(registry.resolve("aov"), registry, _full_olist_model()).measures

    # Revenue over every order -- including an order with no payment row, which the compiler's own
    # docstring records as a real Olist case -- compiled as two independent subqueries.
    assert bound["numerator"].entity == PAYMENTS
    assert bound["denominator"].entity == ORDERS
