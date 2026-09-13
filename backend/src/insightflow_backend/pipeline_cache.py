"""Replaces Phase 5's single global `SessionState` with a dataset-scoped, LRU-bounded pipeline
cache. Phase 5 had exactly one dataset for the whole process; Phase 6 has many (one or more per
project, across many orgs), so caching keys on `dataset_id` instead of being a singleton -- a
re-upload creates a brand-new `Dataset` row (see db/models.py), so it gets its own cache entry
rather than invalidating or colliding with any other project's cached pipelines, which was the
single biggest hazard of the old global-session design.

Building a dataset's pipelines needs its source CSVs as local files (DuckDB/pandas underneath,
not S3-aware) -- `_materialize_local_files` downloads exactly the files the semantic model
actually references, named the way `insightflow_core`'s QueryExecutor expects
(`{source_file}.csv`), the first time a dataset is touched.
"""
import uuid
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from insightflow_backend import wiring
from insightflow_backend.config import settings
from insightflow_backend.db.models import Dataset
from insightflow_backend.storage import ObjectStorage

from insightflow_core.models import SemanticModel as CoreSemanticModel
from insightflow_core.pipeline import AnalyticsEnginePipeline


@dataclass
class _CacheEntry:
    data_dir: Path
    engine: AnalyticsEnginePipeline | None = field(default=None, repr=False)
    dashboard_pipeline: object = field(default=None, repr=False)
    chat_pipeline: object = field(default=None, repr=False)


def _required_filenames(semantic_model: CoreSemanticModel) -> set[str]:
    # Same "most common source_file per entity" rule insightflow_core's QueryExecutor itself
    # uses (execution/query_executor.py) -- duplicated rather than imported because it's a
    # narrow one-liner and importing QueryExecutor internals here would couple this cache to
    # that class's private implementation detail instead of its public register_sources() API.
    names = set()
    for entity in semantic_model.entities:
        if not entity.fields:
            continue
        dominant = Counter(f.source_file for f in entity.fields).most_common(1)[0][0]
        names.add(f"{dominant}.csv")
    return names


class PipelineCache:
    def __init__(self, storage: ObjectStorage, max_datasets: int = 16) -> None:
        self._storage = storage
        self._max_datasets = max_datasets
        self._entries: OrderedDict[uuid.UUID, _CacheEntry] = OrderedDict()

    def _materialize_local_files(self, dataset: Dataset, semantic_model: CoreSemanticModel) -> Path:
        local_dir = settings.dataset_cache_dir / str(dataset.id)
        for filename in _required_filenames(semantic_model):
            key = f"{dataset.storage_prefix}/{filename}"
            self._storage.download_to(key, local_dir / filename)
        return local_dir

    def _get_entry(self, dataset: Dataset) -> _CacheEntry:
        entry = self._entries.get(dataset.id)
        if entry is not None:
            self._entries.move_to_end(dataset.id)
            return entry

        semantic_model = CoreSemanticModel(**dataset.semantic_model)
        entry = _CacheEntry(data_dir=self._materialize_local_files(dataset, semantic_model))
        self._entries[dataset.id] = entry
        if len(self._entries) > self._max_datasets:
            # Evict the least-recently-used dataset's in-memory pipelines only -- its local CSV
            # cache and S3 object are untouched, so a later query just re-triggers a cache miss
            # here rather than losing any data.
            self._entries.popitem(last=False)
        return entry

    def get_or_build_engine(self, dataset: Dataset) -> AnalyticsEnginePipeline:
        entry = self._get_entry(dataset)
        if entry.engine is None:
            semantic_model = CoreSemanticModel(**dataset.semantic_model)
            entry.engine = wiring.build_analytics_engine(semantic_model, str(entry.data_dir))
        return entry.engine

    def get_or_build_dashboard(self, dataset: Dataset):
        entry = self._get_entry(dataset)
        if entry.dashboard_pipeline is None:
            semantic_model = CoreSemanticModel(**dataset.semantic_model)
            entry.dashboard_pipeline = wiring.build_dashboard(semantic_model, str(entry.data_dir))
        return entry.dashboard_pipeline

    def get_or_build_chat(self, dataset: Dataset):
        entry = self._get_entry(dataset)
        if entry.chat_pipeline is None:
            semantic_model = CoreSemanticModel(**dataset.semantic_model)
            engine = self.get_or_build_engine(dataset)
            entry.chat_pipeline = wiring.build_chatbot(semantic_model, str(entry.data_dir), engine)
        return entry.chat_pipeline
