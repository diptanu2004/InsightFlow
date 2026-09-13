"""Deterministic per-category diffing for GROWTH_BY_DIMENSION -- see class_diagram.md's
ResultDiffer and its resolved "category-set mismatch" / "ranking convention" questions.

Zero-fills a dimension value missing from either side (full-outer-join-then-subtract, not an
inner join) rather than dropping it -- a category with revenue in one period and none in the
other is often the strongest possible answer to "what caused the decline," not noise to discard.

Ranks by signed absolute delta (`current - comparison`), not `pct_change`: `pct_change` is
undefined when a zero-filled `comparison_value` is 0, the same divide-by-zero shape
insightflow_core already guards elsewhere with NULLIF (`_compile_ratio`, `_compile_growth`).
`pct_change` is still computed for display, as None (not an error) when undefined.
"""
from typing import Optional

from insightflow_core.models import MetricResult

from insightflow.models.result import CategoryDelta


class ResultDiffer:
    def diff(self, current: MetricResult, comparison: MetricResult, dimension: str) -> list[CategoryDelta]:
        current_values = self._values_by_dimension(current, dimension)
        comparison_values = self._values_by_dimension(comparison, dimension)
        all_dimension_values = set(current_values) | set(comparison_values)

        deltas = []
        for dim_value in all_dimension_values:
            cur = current_values.get(dim_value, 0.0)
            cmp = comparison_values.get(dim_value, 0.0)
            deltas.append(
                CategoryDelta(
                    dimension_value=dim_value,
                    current_value=cur,
                    comparison_value=cmp,
                    delta=cur - cmp,
                    pct_change=self._pct_change(cur, cmp),
                )
            )

        # Most-negative delta first -- see module docstring on why delta, not pct_change, ranks.
        deltas.sort(key=lambda d: d.delta)
        return deltas

    @staticmethod
    def _values_by_dimension(result: MetricResult, dimension: str) -> dict[str, float]:
        if result.rows is None:
            raise ValueError("ResultDiffer.diff requires a grouped MetricResult (rows set, not value)")
        return {str(row[dimension]): float(row["value"]) if row["value"] is not None else 0.0 for row in result.rows}

    @staticmethod
    def _pct_change(current: float, comparison: float) -> Optional[float]:
        if comparison == 0:
            return None
        return (current - comparison) / comparison
