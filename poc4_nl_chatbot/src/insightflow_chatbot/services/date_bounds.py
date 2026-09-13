"""Deterministic date-bounds inference -- see class_diagram.md's resolved "where reference_date
is sourced" question: computed once at pipeline construction, not per-question, not wall-clock
`date.today()`.

Reads the raw source CSV directly rather than going through insightflow_core's engine: the
engine's AGGREGATE path always casts its result to float (QueryExecutor.execute), so a MIN/MAX
measure over a date column isn't a fit -- this is a small, self-contained utility scoped to POC 4
only, not a gap in insightflow_core. Uses the same source_column/source_file resolution
SemanticField already encodes (POC 1's own vocabulary), so it reads the exact same physical
column insightflow_core's QueryExecutor.register_sources would.
"""
import csv
from datetime import date, datetime
from pathlib import Path

from insightflow_core.models import SemanticModel


def infer_date_bounds(
    semantic_model: SemanticModel, data_dir: str, entity_name: str | None, time_field: str
) -> tuple[date, date]:
    """`entity_name=None` resolves the entity per dataset: whichever entity carries `time_field`.

    Phase 8 M4: this used to take a fixed entity name from config (`TIME_ENTITY="orders"`), which
    only existed when a file was literally named orders.csv -- POC 1 names entities after source
    filenames, so a real upload made chat fail to construct at all. Same no-guessing rule as
    insightflow_core's measure binding: a field on more than one entity is refused rather than
    picked, and a configured `entity_name` is the explicit pin that settles it.
    """
    if entity_name is None:
        candidates = [e.name for e in semantic_model.entities if any(f.name == time_field for f in e.fields)]
        if not candidates:
            raise ValueError(f'no entity in this dataset has a "{time_field}" field to infer date bounds from')
        if len(candidates) > 1:
            raise ValueError(
                f'"{time_field}" is on more than one entity ({", ".join(candidates)}), so the date range '
                "for relative time expressions is ambiguous; set TIME_ENTITY to choose one"
            )
        entity_name = candidates[0]

    entity = next((e for e in semantic_model.entities if e.name == entity_name), None)
    if entity is None:
        raise ValueError(f'no entity named "{entity_name}" in the semantic model')
    matches = [f for f in entity.fields if f.name == time_field]
    if not matches:
        raise ValueError(f'entity "{entity_name}" has no field named "{time_field}"')
    # A real semantic model can map one canonical name to more than one physical column (found
    # via this real Olist dataset: "transaction_date" -> both order_purchase_timestamp,
    # confidence 0.98, and order_approved_at, confidence 0.85) -- same highest-confidence
    # tie-break FieldResolver itself uses, not just "whichever came first in the list."
    field = max(matches, key=lambda f: f.confidence)

    csv_path = Path(data_dir) / f"{field.source_file}.csv"
    dates: list[date] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            raw = row[field.source_column]
            if not raw:
                continue
            # fromisoformat (not strptime with a fixed format) because real POC 1 output can map
            # a canonical date field to a full timestamp column (found via this real Olist
            # dataset: "transaction_date" -> orders.order_purchase_timestamp, "2017-04-11
            # 12:22:08") as easily as a bare date column -- Python 3.11+'s fromisoformat accepts
            # both "YYYY-MM-DD" and "YYYY-MM-DD HH:MM:SS" without a format string.
            dates.append(datetime.fromisoformat(raw).date())

    if not dates:
        raise ValueError(f'no rows found in "{csv_path}" to infer date bounds from')
    return min(dates), max(dates)
