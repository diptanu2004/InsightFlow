from difflib import SequenceMatcher

import pandas as pd
from pydantic import BaseModel

from insightflow_schema_discovery.llm.client import LLMClient
from insightflow_schema_discovery.models.relationship import Relationship, RelationshipCandidate
from insightflow_schema_discovery.models.semantic_mapping import SemanticMapping
from typing import Optional


class _RelationshipReasoning(BaseModel):
    is_valid_relationship: bool
    confidence: float


class RelationshipDetector:
    """Candidate generation (statistical) -> LLM reasoning (direction/meaning) ->
    deterministic validation. The LLM is never trusted alone — validate() re-checks
    its output against the original stats before anything becomes a Relationship."""

    def __init__(
        self,
        llm_client: LLMClient,
        name_similarity_threshold: float = 0.6,
        overlap_threshold: float = 0.3,
        top_k_for_llm: int = 10,
    ):
        self.llm_client = llm_client
        self.name_similarity_threshold = name_similarity_threshold
        self.overlap_threshold = overlap_threshold
        self.top_k_for_llm = top_k_for_llm

    def generate_candidates(
        self,
        dataframes: dict[str, pd.DataFrame],
        semantic_mappings: Optional[list[SemanticMapping]] = None,
    ) -> list[RelationshipCandidate]:
        """Two independent paths into candidate generation, deduplicated:

        1. Raw column-name similarity — works when naming conventions align across files.
        2. Shared semantic_type from the mapper — catches genuine relationships between
           columns named too differently for (1) to find (e.g. 'buyer_ref' / 'cust_ref',
           both mapped to 'customer_id'). Value overlap is still required either way; the
           mapper's semantic label alone is never sufficient on its own.
        """
        candidates: list[RelationshipCandidate] = []
        seen: set[tuple] = set()
        files = list(dataframes.keys())

        for i, file_a in enumerate(files):
            for file_b in files[i + 1:]:
                for col_a in dataframes[file_a].columns:
                    for col_b in dataframes[file_b].columns:
                        candidate = self._evaluate_pair(dataframes, file_a, col_a, file_b, col_b, require_name_similarity=True)
                        if candidate is not None:
                            candidates.append(candidate)
                            seen.add((file_a, col_a, file_b, col_b))

        if semantic_mappings:
            by_type: dict[str, list[tuple[str, str]]] = {}
            for m in semantic_mappings:
                if m.semantic_type == "unknown":
                    continue
                by_type.setdefault(m.semantic_type, []).append((m.source_file, m.source_column))

            for locations in by_type.values():
                for idx, (file_a, col_a) in enumerate(locations):
                    for file_b, col_b in locations[idx + 1:]:
                        if file_a == file_b:
                            continue
                        key, reverse_key = (file_a, col_a, file_b, col_b), (file_b, col_b, file_a, col_a)
                        if key in seen or reverse_key in seen:
                            continue
                        candidate = self._evaluate_pair(dataframes, file_a, col_a, file_b, col_b, require_name_similarity=False)
                        if candidate is not None:
                            candidates.append(candidate)
                            seen.add(key)

        candidates.sort(key=lambda c: (c.value_overlap_ratio, c.name_similarity), reverse=True)
        return candidates

    def _evaluate_pair(self, dataframes, file_a, col_a, file_b, col_b, require_name_similarity: bool = True) -> RelationshipCandidate | None:
        sim = self._name_similarity(col_a, col_b)
        if require_name_similarity and sim < self.name_similarity_threshold:
            return None

        series_a, series_b = dataframes[file_a][col_a], dataframes[file_b][col_b]
        overlap = self._value_overlap(series_a, series_b)
        if overlap < self.overlap_threshold:
            return None

        return RelationshipCandidate(
            from_column=col_a,
            from_file=file_a,
            to_column=col_b,
            to_file=file_b,
            name_similarity=round(sim, 4),
            type_compatible=series_a.dtype == series_b.dtype,
            value_overlap_ratio=round(overlap, 4),
            from_uniqueness=round(series_a.nunique() / max(len(series_a), 1), 4),
            to_uniqueness=round(series_b.nunique() / max(len(series_b), 1), 4),
        )

    def reason_with_llm(self, candidates: list[RelationshipCandidate]) -> list[RelationshipCandidate]:
        for candidate in candidates[: self.top_k_for_llm]:
            result = self.llm_client.generate_structured(self._build_prompt(candidate), _RelationshipReasoning)
            candidate.llm_confidence = result.confidence if result.is_valid_relationship else 0.0
        return candidates

    def validate(self, candidates: list[RelationshipCandidate]) -> list[Relationship]:
        """Direction is decided here, deterministically, from uniqueness — never from the
        LLM's free-text guess. The lower-uniqueness column is the FK ("from"); the
        higher-uniqueness column is the PK-like side ("to"). This is independent of the
        arbitrary alphabetical file order candidates were generated in."""
        relationships: list[Relationship] = []
        for c in candidates:
            if not c.llm_confidence:
                continue
            if c.value_overlap_ratio < self.overlap_threshold:  # deterministic re-check
                continue

            if c.from_uniqueness <= c.to_uniqueness:
                from_field, to_field = f"{c.from_file}.{c.from_column}", f"{c.to_file}.{c.to_column}"
            else:
                from_field, to_field = f"{c.to_file}.{c.to_column}", f"{c.from_file}.{c.from_column}"

            relationships.append(
                Relationship(
                    from_field=from_field,
                    to_field=to_field,
                    confidence=round((c.llm_confidence + c.value_overlap_ratio) / 2, 4),
                    direction=f"{from_field} -> {to_field}",
                    validated=True,
                )
            )
        return relationships

    @staticmethod
    def _name_similarity(a: str, b: str) -> float:
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    @staticmethod
    def _value_overlap(series_a: pd.Series, series_b: pd.Series) -> float:
        set_a, set_b = set(series_a.dropna().astype(str)), set(series_b.dropna().astype(str))
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / min(len(set_a), len(set_b))

    @staticmethod
    def _build_prompt(c: RelationshipCandidate) -> str:
        return (
            "Two columns from different files are statistically similar. Decide if this is a genuine "
            "foreign-key relationship (same real-world entity referenced from both files) as opposed to "
            "coincidental overlap.\n\n"
            f"Column A: {c.from_file}.{c.from_column}\n"
            f"Column B: {c.to_file}.{c.to_column}\n"
            f"Name similarity: {c.name_similarity}\n"
            f"Value overlap ratio: {c.value_overlap_ratio}\n"
            f"Type compatible: {c.type_compatible}\n\n"
            "Respond with is_valid_relationship (true/false) and confidence (0-1)."
        )
