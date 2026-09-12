from pydantic import BaseModel

from insightflow.evaluation.ground_truth import GroundTruth
from insightflow.models.semantic_model import SemanticModel


class EvaluationReport(BaseModel):
    mapping_precision: float
    mapping_recall: float
    relationship_precision: float
    relationship_recall: float

    def summary(self) -> str:
        return (
            f"Mapping   -> precision: {self.mapping_precision:.2f}, recall: {self.mapping_recall:.2f}\n"
            f"Relations -> precision: {self.relationship_precision:.2f}, recall: {self.relationship_recall:.2f}"
        )


class Evaluator:
    """Scores a SemanticModel against hand-labeled ground truth. Deliberately decoupled
    from SchemaDiscoveryPipeline so it can run standalone against saved model JSON."""

    def evaluate(self, model: SemanticModel, ground_truth: GroundTruth) -> EvaluationReport:
        predicted_mappings = {(f.source_file, f.source_column, f.name) for e in model.entities for f in e.fields}
        expected_mappings = {
            (m.source_file, m.source_column, m.semantic_type) for m in ground_truth.expected_mappings
        }
        m_precision, m_recall = self._precision_recall(predicted_mappings, expected_mappings)

        predicted_rels = {(r.from_field, r.to_field) for r in model.relationships}
        expected_rels = {(r.from_field, r.to_field) for r in ground_truth.expected_relationships}
        r_precision, r_recall = self._precision_recall(predicted_rels, expected_rels)

        return EvaluationReport(
            mapping_precision=m_precision,
            mapping_recall=m_recall,
            relationship_precision=r_precision,
            relationship_recall=r_recall,
        )

    @staticmethod
    def _precision_recall(predicted: set, expected: set) -> tuple[float, float]:
        if not predicted and not expected:
            return 1.0, 1.0
        true_positives = len(predicted & expected)
        precision = true_positives / len(predicted) if predicted else 0.0
        recall = true_positives / len(expected) if expected else 0.0
        return round(precision, 4), round(recall, 4)
