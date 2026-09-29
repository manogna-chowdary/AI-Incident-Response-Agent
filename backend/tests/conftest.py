"""Shared fixtures for the API tests.

Two things are kept away from the developer's real environment:

* **The database.** Tests run against a separate ``incident_response_test``
  database, created on demand and dropped/recreated per session. Pointing the test
  suite at the development database would risk wiping real incident rows.
* **Hindsight and Groq.** Both are stubbed at the ``app.routes`` boundary. The
  approval endpoint must not depend on a network call, and tests must not spend
  API quota or pollute the real memory bank.

``approved`` writes are asserted against Postgres, not against a mock, because
the whole point of the endpoint is what it does to the stored row.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import routes
from app.config import settings
from app.db import Base
from hindsight_client import RecallResponse
from hindsight_client_api.models.recall_result import RecallResult

from app.groq import IncidentAnalysis, build_messages
from app.main import app
from app.schemas import AnalysisConfidence

TEST_DB_NAME = "incident_response_test"


def _test_database_url() -> str:
    url = str(settings.database_url)
    head, _, _ = url.rpartition("/")
    return f"{head}/{TEST_DB_NAME}"


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _test_database() -> AsyncIterator[None]:
    """Create the test database once, dropping it at the end."""
    url = _test_database_url()
    admin_url = url.rsplit("/", 1)[0] + "/postgres"

    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": TEST_DB_NAME},
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    finally:
        await admin.dispose()

    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    finally:
        await engine.dispose()

    # The database is only dropped once nothing holds a connection to it.
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))
    finally:
        await admin.dispose()


@pytest_asyncio.fixture
async def test_engine(_test_database: None) -> AsyncIterator[AsyncEngine]:
    """One engine per test, with the incidents table emptied on the way out.

    Teardown is attached here rather than to `session` so that tests which only use
    the HTTP client still leave the database clean for the next test.
    """
    engine = create_async_engine(_test_database_url())
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE TABLE incidents RESTART IDENTITY CASCADE"))
        await engine.dispose()


@pytest_asyncio.fixture
async def session(test_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A direct database session, for asserting on the stored row."""
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s


@pytest_asyncio.fixture
async def client(
    test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client bound to the app, with Hindsight and Groq stubbed."""
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

    class _StubHindsight:
        """Stands in for the Hindsight client. Records what it was asked to do.

        Keeps a real in-memory store keyed by document_id so the duplicate-check
        tests exercise the same code path the live service does: /learn scans the
        bank with alist_memories and skips the write when the incident is already
        there.
        """

        def __init__(self) -> None:
            self.retained: list[str] = []
            self.recalled: list[str] = []
            self.retain_error: Exception | None = None
            self.retain_calls: list[dict] = []
            self.list_error: Exception | None = None
            self.recall_error: Exception | None = None
            # When set, overrides the bank as the recall result set.
            self.recall_results: list[str] | None = None
            self.store: dict[str, object] = {}
            self._seq = 0

        async def aget_bank_config(self, bank_id: str) -> None:
            """Report an existing bank so ensure_bank never writes config."""
            return None

        async def aretain(
            self,
            bank_id: str,
            content: str,
            document_id: str | None = None,
            update_mode: str | None = None,
        ) -> None:
            if self.retain_error is not None:
                raise self.retain_error
            self.retained.append(content)
            self.retain_calls.append(
                {"content": content, "document_id": document_id, "update_mode": update_mode}
            )
            self._seq += 1
            # update_mode="replace" overwrites in place, mirroring the real service.
            self.store[document_id or f"anon-{self._seq}"] = SimpleNamespace(
                id=f"mem-{self._seq}", text=content, document_id=document_id
            )

        async def alist_memories(self, bank_id: str, **_kwargs):
            if self.list_error is not None:
                raise self.list_error
            return SimpleNamespace(items=list(self.store.values()), total=len(self.store))

        async def arecall(self, bank_id: str, query: str):
            self.recalled.append(query)
            if self.recall_error is not None:
                raise self.recall_error
            # Recall normally returns the bank, so a memory /learn stored shows up
            # here exactly as it would live. Tests can override it to return a fixed
            # set when they need to control what is retrieved.
            if self.recall_results is not None:
                return RecallResponse(
                    results=[
                        RecallResult(id=f"mem-{i}", text=t, type="world")
                        for i, t in enumerate(self.recall_results)
                    ]
                )

            return RecallResponse(
                results=[
                    RecallResult(
                        id=f"mem-{item.id}",
                        text=item.text,
                        type="world",
                    )
                    for item in self.store.values()
                ]
            )

    class _StubGroq:
        """Stands in for the Groq client and returns a canned analysis."""

        def __init__(self) -> None:
            self.calls = 0
            # What was actually handed to the model, so tests can assert on the
            # evidence it was given rather than on the canned reply.
            self.seen_memories: list[object] = []
            self.messages: list[dict[str, str]] = []

        async def analyze(self, incident, past_experiences) -> IncidentAnalysis:
            self.calls += 1
            self.seen_memories = list(past_experiences)
            self.messages = build_messages(incident, past_experiences)
            return IncidentAnalysis(
                analysis="Stubbed analysis for tests.",
                likely_root_cause=None,
                recommended_resolution=None,
                confidence=AnalysisConfidence.LOW,
            )

    hindsight = _StubHindsight()
    groq = _StubGroq()

    async def _get_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    # routes.py calls get_client() synchronously, so this must not be a coroutine.
    def _get_client() -> _StubHindsight:
        return hindsight

    async def _analyze(incident, past_experiences) -> IncidentAnalysis:
        return await groq.analyze(incident, past_experiences)

    monkeypatch.setattr(routes, "get_client", _get_client)
    monkeypatch.setattr(routes, "analyze_with_groq", _analyze)
    app.dependency_overrides[routes.get_session] = _get_session

    # Expose the stubs so tests can assert on what was (or was not) written.
    app.state.hindsight = hindsight
    app.state.groq = groq

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            c.hindsight = hindsight  # type: ignore[attr-defined]
            c.groq = groq  # type: ignore[attr-defined]
            yield c
    finally:
        app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def incident(client: httpx.AsyncClient) -> dict:
    """A freshly created incident, in the default 'open' state."""
    response = await client.post(
        "/incidents",
        json={
            "service": "payment-api",
            "description": "Payment API returning 503s",
            "severity": "high",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def unique_service() -> str:
    return f"service-{uuid.uuid4().hex[:8]}"
