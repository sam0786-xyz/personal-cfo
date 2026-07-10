"""Shared fixtures: pinned test environment and an in-memory Firestore fake."""

import os

# Pin the environment BEFORE any app import. load_dotenv() does not override
# existing variables, so this also shields tests from a local .env file.
os.environ["CFO_ACCESS_TOKEN"] = "test-token"
os.environ["CFO_AUTH_DISABLED"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["GCP_PROJECT"] = "test-project"
os.environ["CFO_TARGET_DATE"] = ""
os.environ["PUBSUB_AUDIENCE"] = ""
os.environ["PUBSUB_SERVICE_ACCOUNT"] = ""

import pytest  # noqa: E402

from app import state as state_module  # noqa: E402


class FakeSnapshot:
    def __init__(self, data):
        self._data = data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class FakeDocRef:
    def __init__(self, store, key):
        self._store = store
        self._key = key

    def get(self, transaction=None):
        return FakeSnapshot(self._store.get(self._key))

    def set(self, data):
        self._store[self._key] = dict(data)


class FakeCollection:
    def __init__(self, store, name):
        self._store = store
        self._name = name

    def document(self, doc_id):
        return FakeDocRef(self._store, f"{self._name}/{doc_id}")


class FakeDb:
    def __init__(self):
        self.store = {}

    def collection(self, name):
        return FakeCollection(self.store, name)

    def docs_in(self, collection_name):
        prefix = f"{collection_name}/"
        return {k[len(prefix):]: v for k, v in self.store.items() if k.startswith(prefix)}


class FakeTransaction:
    def set(self, ref, data):
        ref.set(data)


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(state_module, "_db", db)
    monkeypatch.setattr(state_module, "run_transaction", lambda callback: callback(FakeTransaction()))
    return db


@pytest.fixture
def client(fake_db):
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


AUTH_HEADERS = {"Authorization": "Bearer test-token"}
