"""Real MinIO, not a mock -- same "verify against the real thing" discipline as tests/db.
Requires `docker compose up -d minio minio-init` running locally; skipped otherwise.
"""
import uuid

import pytest
from botocore.exceptions import EndpointConnectionError

from insightflow_backend.config import settings
from insightflow_backend.storage import build_storage


def _minio_available() -> bool:
    try:
        build_storage().exists("__connectivity_probe__")
        return True
    except EndpointConnectionError:
        return False


requires_minio = pytest.mark.skipif(
    not _minio_available(), reason="requires `docker compose up -d minio minio-init` running locally"
)


@pytest.fixture
def storage(tmp_path):
    original_cache_dir = settings.dataset_cache_dir
    settings.dataset_cache_dir = tmp_path / "dataset_cache"
    store = build_storage()
    yield store
    settings.dataset_cache_dir = original_cache_dir


@requires_minio
def test_save_and_exists(storage):
    key = f"test-org/test-project/{uuid.uuid4()}/orders.csv"
    assert storage.exists(key) is False

    storage.save(key, b"id,amount\n1,10.0\n")
    assert storage.exists(key) is True


@requires_minio
def test_download_to_local_round_trips_bytes(storage):
    key = f"test-org/test-project/{uuid.uuid4()}/orders.csv"
    content = b"id,amount\n1,10.0\n2,20.0\n"
    storage.save(key, content)

    local_path = storage.download_to_local(key)
    assert local_path.read_bytes() == content


@requires_minio
def test_download_to_local_is_cached(storage):
    key = f"test-org/test-project/{uuid.uuid4()}/orders.csv"
    storage.save(key, b"original content")

    first_path = storage.download_to_local(key)
    # Overwrite the object in S3 without touching the local cache -- a second download should
    # still return the (now stale) cached file rather than re-fetching, per download_to_local's
    # own contract ("downloading it first if not already present locally").
    storage.save(key, b"changed content")
    second_path = storage.download_to_local(key)

    assert first_path == second_path
    assert second_path.read_bytes() == b"original content"


@requires_minio
def test_different_keys_do_not_collide_in_local_cache(storage):
    key_a = f"org-a/proj/{uuid.uuid4()}/data.csv"
    key_b = f"org-b/proj/{uuid.uuid4()}/data.csv"
    storage.save(key_a, b"from a")
    storage.save(key_b, b"from b")

    path_a = storage.download_to_local(key_a)
    path_b = storage.download_to_local(key_b)
    assert path_a != path_b
    assert path_a.read_bytes() == b"from a"
    assert path_b.read_bytes() == b"from b"


@requires_minio
def test_exists_returns_false_for_unknown_key(storage):
    assert storage.exists(f"never-written/{uuid.uuid4()}") is False
