from typing import Optional

from insightflow.inference.type_inferer import TypeInferer
from insightflow.llm.client import LLMClient
from insightflow.llm.groq_client import GroqLLMClient
from insightflow.models.semantic_mapping import SemanticMapping
from insightflow.models.semantic_model import Entity, SemanticField, SemanticModel
from insightflow.parsing.file_parser import FileParser
from insightflow.profiling.data_profiler import DataProfiler
from insightflow.relationships.relationship_detector import RelationshipDetector
from insightflow.semantic.semantic_mapper import SemanticMapper


class SchemaDiscoveryPipeline:
    """Orchestrates POC 1 end to end: parse -> profile -> infer types -> map semantics
    -> detect relationships -> assemble the semantic model.

    This is the single entry point a future FastAPI route calls directly, unchanged:

        pipeline = SchemaDiscoveryPipeline()
        model = pipeline.run(filepaths)
        return model   # SemanticModel is already a pydantic BaseModel

    All dependencies are injectable so tests / alternate providers can swap any stage.
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        parser: Optional[FileParser] = None,
        profiler: Optional[DataProfiler] = None,
        type_inferer: Optional[TypeInferer] = None,
        semantic_mapper: Optional[SemanticMapper] = None,
        relationship_detector: Optional[RelationshipDetector] = None,
    ):
        self.llm_client = llm_client or GroqLLMClient()
        self.parser = parser or FileParser()
        self.profiler = profiler or DataProfiler()
        self.type_inferer = type_inferer or TypeInferer()
        self.semantic_mapper = semantic_mapper or SemanticMapper(self.llm_client)
        self.relationship_detector = relationship_detector or RelationshipDetector(self.llm_client)

    def run(self, filepaths: list[str]) -> SemanticModel:
        dataframes = self.parser.parse_all(filepaths)

        all_mappings: list[SemanticMapping] = []
        for file_name, df in dataframes.items():
            profiles = self.profiler.profile(df)
            for profile in profiles:
                self.type_inferer.infer(profile)  # computed for future use/logging; not yet persisted
            all_mappings.extend(self.semantic_mapper.map_columns(file_name, profiles))

        candidates = self.relationship_detector.generate_candidates(dataframes, all_mappings)
        candidates = self.relationship_detector.reason_with_llm(candidates)
        relationships = self.relationship_detector.validate(candidates)

        return SemanticModel(
            entities=self._group_into_entities(all_mappings),
            relationships=relationships,
        )

    @staticmethod
    def _group_into_entities(mappings: list[SemanticMapping]) -> list[Entity]:
        """v1: one entity per source file. Revisit if a single file should ever
        split into multiple entities."""
        grouped: dict[str, list[SemanticField]] = {}
        for m in mappings:
            grouped.setdefault(m.source_file, []).append(
                SemanticField(
                    name=m.semantic_type,
                    source_column=m.source_column,
                    source_file=m.source_file,
                    confidence=m.confidence,
                )
            )
        return [Entity(name=file_name, fields=fields) for file_name, fields in grouped.items()]
