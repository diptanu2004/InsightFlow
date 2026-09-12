# POC 1 — Class Diagram (revised at closure)
## Schema Discovery + Relationship Detection (InsightFlow)

> **Revision note:** this replaces the original planning-stage diagram to match the
> implementation as it actually stands at POC 1 closure. Changes from the original plan:
> - `Dataset` (a wrapper class for a parsed file + its profiles) was never implemented —
>   the pipeline just passes `dict[str, pd.DataFrame]` around directly. Removed here.
> - `RelationshipCandidate.uniqueness_score` (a single max-of-both value) was replaced with
>   `from_uniqueness`/`to_uniqueness` (per-column), because direction needed to be decided
>   deterministically from which side is more unique — a single max couldn't support that.
> - `RelationshipCandidate.llm_direction` was removed entirely. The LLM's job was
>   simplified to just `is_valid_relationship` + `confidence`; direction is now computed
>   deterministically in `validate()` from uniqueness, not parsed from LLM free text.
> - `generate_candidates` gained a second parameter and a second internal path: it now also
>   takes the mapper's `SemanticMapping` list and generates candidates for columns sharing a
>   semantic type, not just columns with similar names. This is what let `eval_hard` and
>   `eval_real_olist` (cryptically-named real FKs) work at all.
> - The model class holding a semantic field inside `SemanticModel` is named `SemanticField`,
>   not `Field` — `Field` collides with pydantic's own `Field()`.
> - `EvaluationReport.confidence_calibration_score` was never implemented — `Evaluator` only
>   computes precision/recall. Removed here rather than left as an aspirational stub.
> - `GroqLLMClient` (concrete `LLMClient` implementation, backed by LangChain's `ChatGroq` in
>   JSON mode) is now shown explicitly — it didn't exist yet at planning time.

```mermaid
classDiagram
    direction LR

    %% ==========================
    %% Ingestion & Profiling
    %% ==========================

    class FileParser {
        +parse(filepath: str) DataFrame
        +parse_all(filepaths: List~str~) Dict~str, DataFrame~
    }

    class ColumnProfile {
        +column_name: str
        +dtype_raw: str
        +null_pct: float
        +unique_count: int
        +cardinality_ratio: float
        +min_value: Any
        +max_value: Any
        +mean: float
        +stdev: float
        +sample_values: List~Any~
        +looks_like_id: bool
        +looks_like_date: bool
        +looks_like_categorical: bool
        +looks_like_currency: bool
    }

    class DataProfiler {
        +profile(df: DataFrame) List~ColumnProfile~
        -profile_column(series) ColumnProfile
        -looks_like_id(name, cardinality_ratio) bool
        -looks_like_date(name, series) bool
    }

    class ColumnType {
        <<enumeration>>
        IDENTIFIER
        DATE
        NUMERIC_CONTINUOUS
        NUMERIC_CURRENCY
        CATEGORICAL
        TEXT
        BOOLEAN
    }

    class TypeInferer {
        +infer(profile: ColumnProfile) ColumnType
    }

    %% ==========================
    %% Semantic Mapping
    %% ==========================

    class SemanticVocabulary {
        +canonical_fields: Dict~str, str~
        +as_prompt_block() str
    }

    class SemanticMapping {
        +source_column: str
        +source_file: str
        +semantic_type: str
        +confidence: float
        +needs_confirmation: bool
    }

    class SemanticMapper {
        +vocabulary: SemanticVocabulary
        +llm_client: LLMClient
        +confidence_threshold: float
        +map_columns(source_file: str, profiles: List~ColumnProfile~) List~SemanticMapping~
        -build_prompt(profiles) str
    }

    class LLMClient {
        <<interface>>
        +generate_structured(prompt: str, output_schema: Type~T~) T
    }

    class GroqLLMClient {
        -llm: ChatGroq
        +generate_structured(prompt: str, output_schema: Type~T~) T
        -schema_json(schema: Type~BaseModel~) str
    }

    %% ==========================
    %% Relationship Detection
    %% ==========================

    class RelationshipCandidate {
        +from_column: str
        +from_file: str
        +to_column: str
        +to_file: str
        +name_similarity: float
        +type_compatible: bool
        +value_overlap_ratio: float
        +from_uniqueness: float
        +to_uniqueness: float
        +llm_confidence: float
    }

    class Relationship {
        +from_field: str
        +to_field: str
        +confidence: float
        +direction: str
        +validated: bool
    }

    class RelationshipDetector {
        +name_similarity_threshold: float
        +overlap_threshold: float
        +top_k_for_llm: int
        +generate_candidates(dataframes, semantic_mappings) List~RelationshipCandidate~
        +reason_with_llm(candidates) List~RelationshipCandidate~
        +validate(candidates) List~Relationship~
        -evaluate_pair(dataframes, file_a, col_a, file_b, col_b, require_name_similarity) RelationshipCandidate
        -name_similarity(a, b) float
        -value_overlap(series_a, series_b) float
    }

    %% ==========================
    %% Semantic Model
    %% ==========================

    class SemanticField {
        +name: str
        +source_column: str
        +source_file: str
        +confidence: float
    }

    class Entity {
        +name: str
        +fields: List~SemanticField~
    }

    class SemanticModel {
        +entities: List~Entity~
        +relationships: List~Relationship~
    }

    %% ==========================
    %% Orchestration
    %% ==========================

    class SchemaDiscoveryPipeline {
        +llm_client: LLMClient
        +parser: FileParser
        +profiler: DataProfiler
        +type_inferer: TypeInferer
        +semantic_mapper: SemanticMapper
        +relationship_detector: RelationshipDetector
        +run(filepaths: List~str~) SemanticModel
        -group_into_entities(mappings) List~Entity~
    }

    %% ==========================
    %% Evaluation
    %% ==========================

    class GroundTruth {
        +expected_mappings: List~SemanticMapping~
        +expected_relationships: List~Relationship~
        +load(path: str) GroundTruth
    }

    class EvaluationReport {
        +mapping_precision: float
        +mapping_recall: float
        +relationship_precision: float
        +relationship_recall: float
        +summary() str
    }

    class Evaluator {
        +evaluate(model: SemanticModel, ground_truth: GroundTruth) EvaluationReport
        -precision_recall(predicted: set, expected: set) tuple
    }

    %% ==========================
    %% Relationships between classes
    %% ==========================

    FileParser --> "Dict~str,DataFrame~" : produces
    DataProfiler --> ColumnProfile : produces
    TypeInferer ..> ColumnProfile : uses
    TypeInferer --> ColumnType : returns

    LLMClient <|-- GroqLLMClient : implements

    SemanticMapper --> SemanticMapping : produces
    SemanticMapper --> LLMClient : uses
    SemanticMapper --> SemanticVocabulary : uses
    SemanticMapper ..> ColumnProfile : uses

    RelationshipDetector --> RelationshipCandidate : produces
    RelationshipDetector --> Relationship : produces
    RelationshipDetector --> LLMClient : uses
    RelationshipDetector ..> SemanticMapping : uses (2nd candidate path)

    SemanticModel "1" *-- "many" Entity : contains
    Entity "1" *-- "many" SemanticField : contains
    SemanticModel "1" *-- "many" Relationship : contains
    SemanticField ..> SemanticMapping : derived from

    SchemaDiscoveryPipeline --> FileParser : uses
    SchemaDiscoveryPipeline --> DataProfiler : uses
    SchemaDiscoveryPipeline --> TypeInferer : uses
    SchemaDiscoveryPipeline --> SemanticMapper : uses
    SchemaDiscoveryPipeline --> RelationshipDetector : uses
    SchemaDiscoveryPipeline --> SemanticModel : produces

    Evaluator --> GroundTruth : uses
    Evaluator --> SemanticModel : uses
    Evaluator --> EvaluationReport : produces
```

## Notes

- **`LLMClient` is an interface**, not tied to a specific provider — `SemanticMapper` and `RelationshipDetector` depend on it via dependency injection. `GroqLLMClient` is the one concrete implementation that exists today, using LangChain's `ChatGroq` in JSON mode (not function-calling — see the pipeline diagram's revision notes for why).
- **`SemanticMapping` vs `SemanticField`**: `SemanticMapping` is the raw, possibly-unconfirmed output of the mapper (includes `needs_confirmation`); `SemanticField` is what actually lands inside the `SemanticModel` once accepted. This separation is what a future HITL layer will hook into — HITL sits between `SemanticMapping` and `SemanticField`/`Relationship` promotion, without POC 1 needing to know about it yet. This part of the plan held up unchanged through implementation.
- **`RelationshipCandidate` vs `Relationship`**: mirrors the doc's candidate-generation → LLM-reasoning → deterministic-validation flow. Only validated candidates become `Relationship` objects inside the model. Direction is decided in `validate()` from `from_uniqueness`/`to_uniqueness`, not from the LLM.
- **`SchemaDiscoveryPipeline`** is the single orchestration entry point — this is what a future FastAPI endpoint or CLI script calls directly. Unchanged from plan.
- **`Evaluator`** is intentionally decoupled from the pipeline itself so it can be run independently in the eval harness against saved `SemanticModel` JSON output. Unchanged from plan, except it's narrower than planned (precision/recall only, no confidence calibration metric).
