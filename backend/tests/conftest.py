from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from insightflow_backend.main import create_app

SAMPLE_DATA_DIR = Path(__file__).parent.parent / "data" / "raw" / "sample"


@pytest.fixture
def client():
    return TestClient(create_app())  # fresh SessionState per test, no cross-test leakage
