import os
from pathlib import Path


TEST_DB = Path(__file__).resolve().parent / "test_app.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"

from fastapi.testclient import TestClient  # noqa: E402
import pytest  # noqa: E402

from app.main import app  # noqa: E402
from app.db import Base, engine
from app.rate_limit import rate_limiter


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    rate_limiter.login_failures.clear()
    rate_limiter.login_blocked_until.clear()
    rate_limiter.register_attempts.clear()
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()
    for suffix in ("-wal", "-shm"):
        path = Path(str(TEST_DB) + suffix)
        if path.exists():
            path.unlink()
