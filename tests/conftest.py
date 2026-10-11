import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.audit import Store  # noqa: E402
from app.agent import Copilot  # noqa: E402
from app.config import Settings  # noqa: E402
from app.mocks import Backends  # noqa: E402
from app.rag import Retriever, load_sops  # noqa: E402

USERS = json.loads((ROOT / "data" / "users.json").read_text())
CUSTOMERS = json.loads((ROOT / "data" / "crm.json").read_text())


def users_by_role(role, status="active"):
    return [u for u in USERS if u["role"] == role and u["status"] == status]


def manager_of(user):
    return next(u for u in USERS if u["user_id"] == user["manager_id"])


@pytest.fixture(scope="session")
def retriever():
    return Retriever(load_sops(ROOT / "data" / "sops"))


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.db_path = str(tmp_path / "t.db")
    s.retry_base_delay_s = 0.0
    return s


@pytest.fixture
def backends(settings):
    return Backends.from_data(settings.data_dir)


@pytest.fixture
def store(settings):
    st = Store(settings.db_path)
    yield st
    st.close()


@pytest.fixture
def cop(settings, backends, retriever, store):
    return Copilot(settings, backends, retriever, store)


@pytest.fixture
def employee():
    return users_by_role("employee")[0]
