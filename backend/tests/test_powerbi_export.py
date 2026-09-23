"""Phase 9: the Power BI export, tested without Power BI.

Everything that decides a number -- which entity a measure binds to, which way a relationship
points, what the calendar covers, which columns are allowed out -- is settled in build.py against
types this repo owns, so it is all checkable here against real CSVs on disk. What these tests
cannot check is whether Power BI Desktop parses the TMDL; that is the manual parity run in
backend/docs/phase9_powerbi.md, and it is a formatting question, not a correctness one.
"""
import io
import zipfile
from pathlib import Path

import pytest

from insightflow_core.compilation.sql_compiler import SQLCompiler
from insightflow_core.models.registry import AggregationType, Measure, MetricDefinition, MetricKind
from insightflow_core.models.semantic_model import Entity, Relationship, SemanticField, SemanticModel
from insightflow_core.registry import MetricRegistry

from insightflow_backend.powerbi import tmdl
from insightflow_backend.powerbi.build import TIME_FIELD, build_bi_model
from insightflow_backend.powerbi.export import build_export_zip

ORDERS_CSV = """order_id,customer_id,order_total,order_purchase_timestamp
o1,c1,100.00,2023-01-15
o2,c1,50.50,2023-02-20
o3,c2,25.00,2023-03-10
o4,c3,75.25,2023-04-05
o5,c2,10.00,2023-05-30
"""

PAYMENTS_CSV = """order_id,customer_id,amount
o1,c1,100.0
"""

CUSTOMERS_CSV = """customer_id,customer_city
c1,Springfield
c2,Shelbyville
c3,Ogdenville
"""


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    (tmp_path / "orders.csv").write_text(ORDERS_CSV, encoding="utf-8")
    (tmp_path / "customers.csv").write_text(CUSTOMERS_CSV, encoding="utf-8")
    return tmp_path


def semantic_model(revenue_status: str = "auto") -> SemanticModel:
    return SemanticModel(
        entities=[
            Entity(
                name="orders",
                fields=[
                    SemanticField(name="order_id", source_column="order_id", source_file="orders", confidence=0.99),
                    SemanticField(name="customer_id", source_column="customer_id", source_file="orders", confidence=0.95),
                    SemanticField(
                        name="revenue",
                        source_column="order_total",
                        source_file="orders",
                        confidence=0.9,
                        status=revenue_status,
                    ),
                    SemanticField(
                        name=TIME_FIELD,
                        source_column="order_purchase_timestamp",
                        source_file="orders",
                        confidence=0.98,
                    ),
                ],
            ),
            Entity(
                name="customers",
                fields=[
                    SemanticField(name="customer_id", source_column="customer_id", source_file="customers", confidence=0.99),
                    SemanticField(name="region", source_column="customer_city", source_file="customers", confidence=0.9),
                ],
            ),
        ],
        relationships=[
            Relationship(
                from_field="orders.customer_id",
                to_field="customers.customer_id",
                confidence=0.99,
                direction="orders.customer_id -> customers.customer_id",
            )
        ],
    )


def registry() -> MetricRegistry:
    r = MetricRegistry()
    r.register_measure(Measure(name="revenue", source_field="revenue", aggregation=AggregationType.SUM, format="money"))
    r.register_measure(
        Measure(
            name="orders",
            source_field="order_id",
            aggregation=AggregationType.COUNT_DISTINCT,
            colocate_with_field="customer_id",
            format="count",
        )
    )
    r.register_metric(
        MetricDefinition(
            name="aov",
            kind=MetricKind.RATIO,
            numerator_measure="revenue",
            denominator_measure="orders",
            format="money",
        )
    )
    r.register_metric(
        MetricDefinition(name="revenue_growth", kind=MetricKind.GROWTH, base_measure="revenue", format="percent")
    )
    return r


def build(data_dir: Path, model: SemanticModel | None = None):
    return build_bi_model("Test", (model or semantic_model()).trusted(), registry(), data_dir)


def measure(bi_model, name: str):
    return next(m for m in bi_model.measures if m.name == name)


# -- measures mirror the engine -------------------------------------------------------------


def test_measures_bind_to_the_same_entity_the_engine_binds_them_to(data_dir):
    bi = build(data_dir)
    assert measure(bi, "revenue").home_table == "orders"
    # `orders` counts order_id where customer_id lives -- the population hint, not an entity pin.
    assert measure(bi, "orders").home_table == "orders"


def test_sum_is_coalesced_and_count_distinct_ignores_blanks(data_dir):
    """Both departures from idiomatic DAX exist to match SQLCompiler, not taste: it COALESCEs SUM to
    0, and SQL's COUNT(DISTINCT) ignores NULLs where DAX's DISTINCTCOUNT counts BLANK as a value."""
    bi = build(data_dir)
    assert measure(bi, "revenue").dax == "COALESCE(SUM('orders'[order_total]), 0)"
    assert measure(bi, "orders").dax == "DISTINCTCOUNTNOBLANK('orders'[order_id])"


def test_ratio_composes_registered_measures_rather_than_inlining_them(data_dir):
    assert measure(build(data_dir), "aov").dax == "DIVIDE([revenue], [orders])"


def test_growth_measures_the_dataset_not_the_wall_clock(data_dir):
    dax = measure(build(data_dir), "revenue_growth").dax
    assert "TODAY()" not in dax and "NOW()" not in dax
    # The preceding equal-length window, POC 4's own comparison-period convention.
    assert "DATESBETWEEN" in dax and "CurrentStart - WindowDays" in dax


def test_a_metric_the_engine_could_not_resolve_is_omitted_with_its_reason(data_dir, tmp_path):
    """An unresolvable measure is refused here by the same exception the engine raises, so the export
    can't quietly ship a measure that would fail on the same dataset in the platform."""
    model = semantic_model()
    model.entities[1].fields.append(
        SemanticField(name="revenue", source_column="customer_city", source_file="customers", confidence=0.4)
    )
    bi = build(data_dir, model)
    omitted = dict(bi.omitted_metrics)
    assert "revenue" in omitted
    assert "more than one entity" in omitted["revenue"]
    assert all(m.name != "revenue" for m in bi.measures)


def test_time_field_constant_matches_the_compilers(data_dir):
    assert TIME_FIELD == SQLCompiler.TIME_FIELD


# -- what may leave the platform ------------------------------------------------------------


def test_only_trusted_mappings_are_exported(data_dir):
    """The rule that produced a real AOV of 22.82 against a true 160.99 when it was missing: anything
    that computes numbers is built on trusted() only. A DAX measure computes numbers."""
    bi = build(data_dir, semantic_model(revenue_status="rejected"))
    orders = next(t for t in bi.tables if t.name == "orders")
    assert "order_total" not in {c.name for c in orders.columns}
    assert all(m.name != "revenue" for m in bi.measures)


def test_a_join_column_survives_even_with_no_trusted_mapping(data_dir):
    """Mirrors what the SQL safety checker already allows: a review can reject a key column's
    meaning while the physical foreign key is still what joins the tables."""
    model = semantic_model()
    for field in model.entities[1].fields:
        if field.name == "customer_id":
            field.status = "rejected"
    bi = build(data_dir, model)
    customers = next(t for t in bi.tables if t.name == "customers")
    assert "customer_id" in {c.name for c in customers.columns}
    assert any(r.to_table == "customers" and r.to_column == "customer_id" for r in bi.relationships)


def test_column_descriptions_carry_the_mapping_and_its_confidence(data_dir):
    bi = build(data_dir)
    orders = next(t for t in bi.tables if t.name == "orders")
    revenue_column = next(c for c in orders.columns if c.name == "order_total")
    assert revenue_column.canonical_field == "revenue"
    assert revenue_column.confidence == pytest.approx(0.9)


# -- structure Power BI needs that POC 1 doesn't record ---------------------------------------


def test_cardinality_is_measured_from_the_data(data_dir):
    """customer_id repeats in orders and is unique in customers, so orders is the many side. Getting
    this backwards is how a model silently fans a SUM out across the many side."""
    relationship = next(r for r in build(data_dir).relationships if r.to_table == "customers")
    assert (relationship.from_table, relationship.from_column) == ("orders", "customer_id")
    assert relationship.cardinality == "manyToOne"


def test_the_many_side_is_used_even_when_poc1_recorded_the_relationship_the_other_way(data_dir):
    model = semantic_model()
    model.relationships = [
        Relationship(
            from_field="customers.customer_id",
            to_field="orders.customer_id",
            confidence=0.99,
            direction="customers.customer_id -> orders.customer_id",
        )
    ]
    relationship = next(r for r in build(data_dir, model).relationships if r.is_active)
    assert relationship.from_table == "orders"
    assert relationship.to_table == "customers"


def test_a_cycle_leaves_exactly_one_relationship_inactive(data_dir, tmp_path):
    """Power BI allows one active path between two tables; POC 1's output routinely has more (real
    Olist detects order_items -> orders -> payments alongside order_items -> payments)."""
    (tmp_path / "payments.csv").write_text("order_id,customer_id,amount\no1,c1,100.0\n", encoding="utf-8")
    model = semantic_model()
    model.entities.append(
        Entity(
            name="payments",
            fields=[
                SemanticField(name="order_id", source_column="order_id", source_file="payments", confidence=0.99),
                SemanticField(name="customer_id", source_column="customer_id", source_file="payments", confidence=0.9),
            ],
        )
    )
    model.relationships += [
        Relationship(from_field="payments.order_id", to_field="orders.order_id", confidence=0.98, direction="x"),
        Relationship(from_field="payments.customer_id", to_field="customers.customer_id", confidence=0.7, direction="x"),
    ]
    relationships = build(data_dir, model).relationships
    inactive = [r for r in relationships if not r.is_active]
    assert len(inactive) == 1
    # The weakest edge of the cycle is the one dropped, not an arbitrary one.
    assert inactive[0].from_table == "payments" and inactive[0].to_table == "customers"
    assert "already connected" in inactive[0].inactive_reason


def test_calendar_covers_the_datasets_own_range(data_dir):
    date_table = build(data_dir).date_table
    assert date_table is not None
    assert (date_table.start_date.isoformat(), date_table.end_date.isoformat()) == ("2023-01-15", "2023-05-30")
    assert date_table.related_table == "orders"
    assert date_table.related_column == "order_purchase_timestamp"


def test_no_calendar_and_no_growth_when_the_dataset_has_no_date(data_dir):
    model = semantic_model()
    model.entities[0].fields = [f for f in model.entities[0].fields if f.name != TIME_FIELD]
    bi = build(data_dir, model)
    assert bi.date_table is None
    assert "revenue_growth" in dict(bi.omitted_metrics)


def test_column_types_come_from_the_engines_reader(data_dir):
    orders = next(t for t in build(data_dir).tables if t.name == "orders")
    types = {c.name: c.data_type for c in orders.columns}
    assert types["order_total"] == "double"
    assert types["order_purchase_timestamp"] == "dateTime"
    assert types["order_id"] == "string"


# -- serialization --------------------------------------------------------------------------


def test_tmdl_indents_with_tabs_only(data_dir):
    bi = build(data_dir)
    orders = next(t for t in bi.tables if t.name == "orders")
    text = tmdl.table_tmdl(orders, list(bi.measures), orders.delimiter, None, None)
    for line in text.split("\n"):
        assert not line.startswith(" "), f"space-indented TMDL line: {line!r}"


def test_descriptions_precede_the_object_they_describe(data_dir):
    bi = build(data_dir)
    orders = next(t for t in bi.tables if t.name == "orders")
    lines = tmdl.table_tmdl(orders, [], orders.delimiter, None, None).split("\n")
    index = next(i for i, line in enumerate(lines) if line.strip() == "column order_total")
    assert lines[index - 1].strip().startswith("/// InsightFlow mapping: revenue")


def test_relationships_carry_no_description(data_dir, tmp_path):
    """A TMSL Relationship has no `description` property, so a `///` line before one makes Power BI
    Desktop reject the entire project ("Property 'description' is unknown and is not expected in the
    situation it appears") -- found on the first real open in Desktop 2.157. The reason an edge is
    inactive lives in the export README instead.
    """
    (tmp_path / "payments.csv").write_text(PAYMENTS_CSV, encoding="utf-8")
    model = semantic_model()
    model.entities.append(
        Entity(
            name="payments",
            fields=[
                SemanticField(name="order_id", source_column="order_id", source_file="payments", confidence=0.99),
                SemanticField(name="customer_id", source_column="customer_id", source_file="payments", confidence=0.9),
            ],
        )
    )
    model.relationships += [
        Relationship(from_field="payments.order_id", to_field="orders.order_id", confidence=0.98, direction="x"),
        Relationship(from_field="payments.customer_id", to_field="customers.customer_id", confidence=0.7, direction="x"),
    ]
    bi = build(data_dir, model)
    assert any(not r.is_active for r in bi.relationships), "fixture must produce an inactive edge"

    text = tmdl.relationships_tmdl(list(bi.relationships))
    assert "///" not in text
    assert "isActive: false" in text  # the edge is still deactivated, just not annotated


def test_calendar_dax_has_literal_bounds_and_no_today(data_dir):
    text = tmdl.calendar_tmdl(build(data_dir).date_table)
    assert "CALENDAR(DATE(2023, 1, 15), DATE(2023, 5, 30))" in text
    assert "TODAY()" not in text


def test_export_is_reproducible_for_an_unchanged_dataset(data_dir):
    """Lineage tags are derived from each object's own path rather than uuid4, so a re-export of an
    unchanged dataset is byte-identical and a person tracking it in git sees real changes only."""
    first = build_export_zip("Test", semantic_model(), registry(), data_dir)
    second = build_export_zip("Test", semantic_model(), registry(), data_dir)
    assert _text_files(first) == _text_files(second)


def _text_files(blob: bytes) -> dict[str, str]:
    archive = zipfile.ZipFile(io.BytesIO(blob))
    return {n: archive.read(n).decode("utf-8") for n in archive.namelist() if not n.startswith("data/")}


def test_zip_holds_a_complete_project_and_the_source_csvs(data_dir):
    names = set(zipfile.ZipFile(io.BytesIO(build_export_zip("Test", semantic_model(), registry(), data_dir))).namelist())
    assert {
        "Test.pbip",
        "Test.SemanticModel/definition.pbism",
        "Test.SemanticModel/definition/model.tmdl",
        "Test.SemanticModel/definition/relationships.tmdl",
        "Test.SemanticModel/definition/expressions.tmdl",
        "Test.SemanticModel/definition/tables/orders.tmdl",
        "Test.SemanticModel/definition/tables/customers.tmdl",
        "Test.SemanticModel/definition/tables/Calendar.tmdl",
        "Test.Report/definition.pbir",
        "README.md",
        "data/orders.csv",
        "data/customers.csv",
    } <= names


def test_every_table_is_referenced_by_the_model_file(data_dir):
    """A table file Power BI is never told about is silently absent from the model."""
    blob = build_export_zip("Test", semantic_model(), registry(), data_dir)
    files = _text_files(blob)
    referenced = {line.split("ref table ", 1)[1].strip().strip("'") for line in files["Test.SemanticModel/definition/model.tmdl"].split("\n") if line.startswith("ref table ")}
    table_files = {
        n.rsplit("/", 1)[1][: -len(".tmdl")] for n in files if "/definition/tables/" in n
    }
    assert referenced == table_files


def test_readme_states_what_the_export_does_not_carry(data_dir):
    readme = _text_files(build_export_zip("Test", semantic_model(), registry(), data_dir))["README.md"]
    assert "Caveats" in readme
    assert "Access control" in readme
    assert "DataFolder" in readme
