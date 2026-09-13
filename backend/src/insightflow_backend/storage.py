"""S3-compatible object storage for uploaded datasets. Only one backend exists (S3), used
against real S3 in prod and against MinIO everywhere else (docker-compose's `minio` service) --
per the Phase 6 plan's "no local-disk backend kept for prod" choice, so the code path under test
is identical to the code path in production and only the endpoint URL differs.

Keys are namespaced `org_id/project_id/dataset_id/filename` (see db/models.py's
`Dataset.storage_prefix`) -- natural tenant isolation, and doubles as the audit trail for which
org owns a given object.

The POC pipelines underneath (DuckDB/pandas) read local file paths, not S3 URIs, so
`download_to_local` pulls an object down to a per-process temp cache keyed by its S3 key before
any pipeline touches it.
"""
import hashlib
from pathlib import Path
from typing import Protocol

import boto3
from botocore.exceptions import ClientError

from insightflow_backend.config import settings


class ObjectStorage(Protocol):
    def save(self, key: str, data: bytes) -> str:
        """Writes `data` under `key`, returns the key (callers persist this, e.g. in
        Dataset.storage_prefix + filename -- not a presigned URL, which would expire)."""
        ...

    def download_to_local(self, key: str) -> Path:
        """Returns a local filesystem path with the object's bytes, downloading (and caching)
        it first if not already present locally."""
        ...

    def exists(self, key: str) -> bool: ...


class S3ObjectStorage:
    def __init__(
        self,
        bucket: str,
        endpoint_url: str,
        aws_access_key_id: str,
        aws_secret_access_key: str,
        region_name: str,
        local_cache_dir: Path,
    ) -> None:
        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=region_name,
        )
        self._local_cache_dir = local_cache_dir
        self._local_cache_dir.mkdir(parents=True, exist_ok=True)

    def save(self, key: str, data: bytes) -> str:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data)
        return key

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                return False
            raise

    def download_to_local(self, key: str) -> Path:
        # Cache path derived from a hash of the key, not the key itself -- S3 keys contain "/"
        # (org_id/project_id/dataset_id/filename), which would otherwise silently create nested
        # directories that collide with the flat cache layout this is meant to be.
        cache_name = hashlib.sha256(key.encode()).hexdigest() + "_" + Path(key).name
        local_path = self._local_cache_dir / cache_name
        if not local_path.exists():
            self._client.download_file(self._bucket, key, str(local_path))
        return local_path


def build_storage() -> S3ObjectStorage:
    return S3ObjectStorage(
        bucket=settings.s3_bucket,
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        region_name=settings.aws_region,
        local_cache_dir=settings.dataset_cache_dir,
    )
