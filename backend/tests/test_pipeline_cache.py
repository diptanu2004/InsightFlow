"""PipelineCache's own caching/eviction logic, tested with an in-memory storage stub -- the S3
transport it depends on is already verified against real MinIO in tests/test_storage.py, so this
file is about the cache's behavior (identity reuse, per-dataset isolation, LRU eviction), not
about S3 itself.
"""
import uuid

import pytest

from insightflow_backend.db.models import Dataset
from insightflow_backend.pipeline_cache import PipelineCache
from insightflow_core.models import Entity, SemanticField
from insightflow_core.models import SemanticModel as CoreSemanticModel


class _InMemoryStorage:
    """Just enough of the ObjectStorage protocol for PipelineCache: pre-seeded objects, and a
    call counter on download_to so tests can assert caching actually avoids repeat "downloads".
    """

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.download_calls = 0  # actual transfers only, mirroring the real S3 backend's
        # "if not local_path.exists(): download" -- download_to() is called on every cache
        # miss regardless (that's how it decides whether a transfer is needed at all), so
        # counting calls instead of transfers would conflate "asked" with "actually fetched".

    def seed(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def save(self, key, data):
        self.objects[key] = data
        return key

    def exists(self, key):
        return key in self.objects

    def download_to_local(self, key):
        raise NotImplementedError("PipelineCache only uses download_to")

    def download_to(self, key, local_path):
        local_path.parent.mkdir(parents=True, exist_ok=True)
        if not local_path.exists():
            self.download_calls += 1
            local_path.write_bytes(self.objects[key])
        return local_path


def _make_dataset(storage: _InMemoryStorage, csv_bytes: bytes = b"revenue\n10.0\n20.0\n") -> Dataset:
    model = CoreSemanticModel(
        entities=[
            Entity(
                name="orders",
                fields=[SemanticField(name="revenue", source_column="revenue", source_file="orders", confidence=0.9)],
            )
        ],
        relationships=[],
    )
    dataset_id = uuid.uuid4()
    storage_prefix = f"org/proj/{dataset_id}"
    storage.seed(f"{storage_prefix}/orders.csv", csv_bytes)
    return Dataset(
        id=dataset_id,
        project_id=uuid.uuid4(),
        name="test-dataset",
        storage_prefix=storage_prefix,
        semantic_model=model.model_dump(mode="json"),
        created_by=uuid.uuid4(),
    )


@pytest.fixture
def storage():
    return _InMemoryStorage()


@pytest.fixture(autouse=True)
def _isolated_cache_dir(tmp_path, monkeypatch):
    from insightflow_backend.config import settings

    monkeypatch.setattr(settings, "dataset_cache_dir", tmp_path)


def test_get_or_build_engine_caches_by_identity(storage):
    cache = PipelineCache(storage=storage)
    dataset = _make_dataset(storage)

    first = cache.get_or_build_engine(dataset)
    second = cache.get_or_build_engine(dataset)
    assert first is second


def test_engine_build_downloads_files_only_once(storage):
    cache = PipelineCache(storage=storage)
    dataset = _make_dataset(storage)

    cache.get_or_build_engine(dataset)
    cache.get_or_build_engine(dataset)
    assert storage.download_calls == 1


def test_two_datasets_get_independent_engines(storage):
    cache = PipelineCache(storage=storage)
    dataset_a = _make_dataset(storage, csv_bytes=b"revenue\n1.0\n")
    dataset_b = _make_dataset(storage, csv_bytes=b"revenue\n2.0\n")

    engine_a = cache.get_or_build_engine(dataset_a)
    engine_b = cache.get_or_build_engine(dataset_b)
    assert engine_a is not engine_b


def test_lru_eviction_drops_oldest_in_memory_pipeline_but_not_local_files(storage):
    cache = PipelineCache(storage=storage, max_datasets=2)
    dataset_a = _make_dataset(storage)
    dataset_b = _make_dataset(storage)
    dataset_c = _make_dataset(storage)

    engine_a_first = cache.get_or_build_engine(dataset_a)
    cache.get_or_build_engine(dataset_b)
    cache.get_or_build_engine(dataset_c)  # should evict dataset_a's in-memory entry

    calls_before = storage.download_calls
    engine_a_second = cache.get_or_build_engine(dataset_a)

    assert engine_a_first is not engine_a_second  # rebuilt, not reused
    # Local CSV cache for dataset_a survives eviction (only the pipeline object was evicted),
    # so rebuilding it doesn't need to hit storage again.
    assert storage.download_calls == calls_before
