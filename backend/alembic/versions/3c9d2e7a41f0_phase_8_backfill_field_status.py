"""phase 8 backfill semantic field status

Data-only migration. Phase 8 added `SemanticField.status` so POC 1's needs-confirmation decision stops
being dropped at the semantic-model boundary (the engine now computes only on "auto" or "confirmed"
mappings -- see insightflow_core's SemanticModel.trusted). The model defaults a missing status to
"auto" for terse test fixtures; without this backfill every dataset stored before the change would
therefore have had *all* of its mappings silently trusted, low-confidence guesses included -- the
exact failure that produced a 22.82 AOV against a true 160.99 on a real Olist upload.

0.75 is POC 1's CONFIDENCE_THRESHOLD default, frozen here on purpose: a migration records what the
rule was when it ran, it shouldn't change meaning if the setting changes later.

Revision ID: 3c9d2e7a41f0
Revises: 678e27211cd0
Create Date: 2026-09-13 23:10:00.000000

"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "3c9d2e7a41f0"
down_revision: Union[str, None] = "678e27211cd0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONFIDENCE_THRESHOLD = 0.75


def _rewrite(transform) -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, semantic_model FROM datasets")).fetchall()
    for dataset_id, model in rows:
        model = model if isinstance(model, dict) else json.loads(model)
        changed = False
        for entity in model.get("entities", []):
            for field in entity.get("fields", []):
                changed |= transform(field)
        if changed:
            conn.execute(
                sa.text("UPDATE datasets SET semantic_model = CAST(:model AS jsonb) WHERE id = :id"),
                {"model": json.dumps(model), "id": dataset_id},
            )


def upgrade() -> None:
    def backfill(field: dict) -> bool:
        if "status" in field:
            return False
        field["status"] = "auto" if field.get("confidence", 0) >= _CONFIDENCE_THRESHOLD else "needs_confirmation"
        return True

    _rewrite(backfill)


def downgrade() -> None:
    # Irreversible in one respect: any human confirm/reject decisions are discarded with the key.
    def strip(field: dict) -> bool:
        return field.pop("status", None) is not None

    _rewrite(strip)
