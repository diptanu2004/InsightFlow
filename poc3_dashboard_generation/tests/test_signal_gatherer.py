from datetime import date

from insightflow_dashboard.dashboard.signal import SignalGatherer


def _by_name(signals):
    return {s.name: s.result for s in signals}


def test_base_kpi_signals_match_hand_computed_totals(engine, semantic_model, registry):
    gatherer = SignalGatherer(engine, semantic_model, registry, reference_date=date(2026, 3, 1))
    results = _by_name(gatherer.gather())
    # data/raw/sample/orders.csv: 100+150+200+50+80+120+90 = 790 revenue, 7 orders, 4 customers
    assert results["total_revenue"].value == 790.0
    assert results["total_orders"].value == 7.0
    assert results["total_customers"].value == 4.0


def test_dimension_signals_match_hand_computed_breakdown(engine, semantic_model, registry):
    gatherer = SignalGatherer(engine, semantic_model, registry, reference_date=date(2026, 3, 1))
    results = _by_name(gatherer.gather())
    # products: P1/P2 = Gadgets (100+150+200+90=540... see below), P3 = Home
    # Gadgets: O1(100,P1)+O2(150,P2)+O3(200,P1)+O6(120,P2)+O7(90,P1) = 660; Home: O4(50)+O5(80) = 130
    assert results["top_category_by_revenue"].rows == [{"category": "Gadgets", "value": 660.0}]
    assert results["bottom_category_by_revenue"].rows == [{"category": "Home", "value": 130.0}]
    # regions: North (C1+C3) = 100+150+80+50 = 380; South (C2) = 200+120 = 320; West (C4) = 90
    assert results["top_region_by_revenue"].rows == [{"region": "North", "value": 380.0}]
    assert results["bottom_region_by_revenue"].rows == [{"region": "West", "value": 90.0}]


def test_growth_signals_are_feasible_with_a_reference_date_matching_the_data(engine, semantic_model, registry):
    gatherer = SignalGatherer(engine, semantic_model, registry, reference_date=date(2026, 3, 1))
    results = _by_name(gatherer.gather())
    assert "revenue_growth" in results
    assert "order_growth" in results
    assert "customer_growth" in results
    assert results["revenue_growth"].value is not None


def test_growth_signals_gracefully_vanish_far_from_the_data(engine, semantic_model, registry):
    """See signal.py's module docstring: a reference_date far outside the dataset's own date
    range pushes the comparison window to zero matching rows, which is POC 2's own known
    undefined-ratio gap (crashes MetricResult rather than returning None). SignalGatherer must
    swallow that, not propagate it -- the rest of the signals should still come back."""
    gatherer = SignalGatherer(engine, semantic_model, registry, reference_date=date(2030, 1, 1))
    names = {s.name for s in gatherer.gather()}
    assert "revenue_growth" not in names
    assert "total_revenue" in names  # unaffected signals still come back


def test_dimension_signals_are_skipped_when_dimension_does_not_exist(engine, registry):
    from insightflow_core.models.semantic_model import Entity, SemanticField, SemanticModel

    # A semantic model with no `category`/`region` field anywhere -- dimension signals must not
    # be attempted at all (not just fail silently), per fixed_signals()'s feasibility check.
    stripped = SemanticModel(
        entities=[
            Entity(
                name="orders",
                fields=[
                    SemanticField(name="order_id", source_column="order_id", source_file="orders", confidence=1.0),
                    SemanticField(name="customer_id", source_column="customer_id", source_file="orders", confidence=1.0),
                    SemanticField(name="revenue", source_column="revenue", source_file="orders", confidence=1.0),
                    SemanticField(name="transaction_date", source_column="order_date", source_file="orders", confidence=1.0),
                ],
            )
        ],
        relationships=[],
    )
    gatherer = SignalGatherer(engine, stripped, registry, reference_date=date(2026, 3, 1))
    names = {s.name for s in gatherer.gather()}
    assert not any("category" in n or "region" in n for n in names)
    assert "total_revenue" in names


def test_composite_kpis_are_signals_so_the_planner_sees_their_real_values(engine, semantic_model, registry):
    """Phase 8: the planner wrote "the low repeat purchase rate reveals ..." about a rate it had never
    seen. It can only ground a claim in a value it was actually given."""
    results = {r.name: r.result for r in SignalGatherer(engine, semantic_model, registry, reference_date=date(2026, 3, 1)).gather()}
    assert "average_order_value" in results
    assert "repeat_purchase_rate" in results


def test_without_a_reference_date_growth_signals_are_skipped_not_measured_from_today(engine, semantic_model, registry):
    """A wall-clock default measured Olist (ends 2018) from today, so every growth window was empty."""
    names = {r.name for r in SignalGatherer(engine, semantic_model, registry).gather()}
    assert not {"revenue_growth", "order_growth", "customer_growth"} & names
    assert "total_revenue" in names
