"""State — persistence for goals and executions.

Backends:
  memory://                    InMemoryStore   (default; zero deps; dev/tests)
  sqlite+aiosqlite:///file.db  SQLStore        (`pip install agentos[database]`)
  postgresql+asyncpg://…       SQLStore        (production)

The rest of AgentOS only ever talks to BaseStore, so backends are
hot-swappable by configuration alone.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

try:
    from sqlalchemy import JSON, DateTime, String, select
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

    _HAS_SQLALCHEMY = True
except ImportError:  # pragma: no cover - only hit without the [database] extra
    _HAS_SQLALCHEMY = False

from agentos.models import Execution, Goal


class BaseStore(ABC):
    """Persistence contract for goals and executions."""

    @abstractmethod
    async def save_goal(self, goal: Goal) -> None: ...

    @abstractmethod
    async def get_goal(self, goal_id: str) -> Goal | None: ...

    @abstractmethod
    async def list_goals(self, limit: int = 100, offset: int = 0) -> list[Goal]: ...

    @abstractmethod
    async def save_execution(self, execution: Execution) -> None: ...

    @abstractmethod
    async def get_execution(self, execution_id: str) -> Execution | None: ...

    @abstractmethod
    async def list_executions(
        self,
        goal_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Execution]: ...


class InMemoryStore(BaseStore):
    """Async-safe in-process store. Default for dev, tests, and demos."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._goals: dict[str, Goal] = {}
        self._executions: dict[str, Execution] = {}

    async def save_goal(self, goal: Goal) -> None:
        async with self._lock:
            self._goals[goal.id] = goal.model_copy(deep=True)

    async def get_goal(self, goal_id: str) -> Goal | None:
        async with self._lock:
            goal = self._goals.get(goal_id)
            return goal.model_copy(deep=True) if goal else None

    async def list_goals(self, limit: int = 100, offset: int = 0) -> list[Goal]:
        async with self._lock:
            goals = sorted(self._goals.values(), key=lambda g: g.created_at, reverse=True)
            return [g.model_copy(deep=True) for g in goals[offset : offset + limit]]

    async def save_execution(self, execution: Execution) -> None:
        async with self._lock:
            self._executions[execution.id] = execution.model_copy(deep=True)

    async def get_execution(self, execution_id: str) -> Execution | None:
        async with self._lock:
            execution = self._executions.get(execution_id)
            return execution.model_copy(deep=True) if execution else None

    async def list_executions(
        self,
        goal_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Execution]:
        async with self._lock:
            executions = list(self._executions.values())
        if goal_id is not None:
            executions = [e for e in executions if e.goal_id == goal_id]
        if status is not None:
            executions = [e for e in executions if e.status == status]
        executions.sort(key=lambda e: e.started_at, reverse=True)
        return [e.model_copy(deep=True) for e in executions[offset : offset + limit]]

    async def clear(self) -> None:
        """Test helper: wipe all state."""
        async with self._lock:
            self._goals.clear()
            self._executions.clear()


def _utcnow() -> datetime:
    return datetime.now(UTC)


if _HAS_SQLALCHEMY:

    class _Base(DeclarativeBase):  # type: ignore[misc,valid-type]
        pass

    class GoalRow(_Base):
        __tablename__ = "goals"

        id: Mapped[str] = mapped_column(String(64), primary_key=True)
        data: Mapped[dict] = mapped_column(JSON)
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    class ExecRow(_Base):
        __tablename__ = "executions"

        id: Mapped[str] = mapped_column(String(64), primary_key=True)
        goal_id: Mapped[str] = mapped_column(String(64), index=True)
        status: Mapped[str] = mapped_column(String(32), index=True)
        data: Mapped[dict] = mapped_column(JSON)
        started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


if _HAS_SQLALCHEMY:

    class SQLStore(BaseStore):  # type: ignore[no-redef]
        """Async SQLAlchemy store.

        Rows serialise to a JSON document column: AgentOS reads/writes whole
        aggregates by id, so a document shape keeps the schema stable while
        models evolve. Indexes cover the hot filters (goal_id, status).
        """

        def __init__(self, database_url: str) -> None:
            kwargs: dict[str, Any] = {}
            if database_url.startswith("sqlite"):
                kwargs["connect_args"] = {"timeout": 30}
            from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

            self._engine: AsyncEngine = create_async_engine(database_url, **kwargs)
            self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

        async def create_tables(self) -> None:
            async with self._engine.begin() as conn:
                await conn.run_sync(_Base.metadata.create_all)

        async def close(self) -> None:
            await self._engine.dispose()

        async def _upsert(self, table: type[Any], payload: dict[str, Any], pk: str) -> None:
            async with self._session_factory() as session:
                async with session.begin():
                    stmt = sqlite_insert(table).values(**payload)
                    set_ = {c.key: stmt.excluded[c.key] for c in table.__table__.columns if c.key != pk}
                    stmt = stmt.on_conflict_do_update(index_elements=[pk], set_=set_)
                    await session.execute(stmt)

        async def save_goal(self, goal: Goal) -> None:
            await self._upsert(
                GoalRow,
                {"id": goal.id, "data": goal.model_dump(mode="json"), "created_at": goal.created_at},
                "id",
            )

        async def get_goal(self, goal_id: str) -> Goal | None:
            async with self._session_factory() as session:
                row = (
                    await session.execute(select(GoalRow).where(GoalRow.id == goal_id))
                ).scalar_one_or_none()
                return Goal.model_validate(row.data) if row else None

        async def list_goals(self, limit: int = 100, offset: int = 0) -> list[Goal]:
            async with self._session_factory() as session:
                rows = (
                    (
                        await session.execute(
                            select(GoalRow).order_by(GoalRow.created_at.desc()).limit(limit).offset(offset)
                        )
                    )
                    .scalars()
                    .all()
                )
                return [Goal.model_validate(r.data) for r in rows]

        async def save_execution(self, execution: Execution) -> None:
            await self._upsert(
                ExecRow,
                {
                    "id": execution.id,
                    "goal_id": execution.goal_id,
                    "status": execution.status,
                    "data": execution.model_dump(mode="json"),
                    "started_at": execution.started_at,
                },
                "id",
            )

        async def get_execution(self, execution_id: str) -> Execution | None:
            async with self._session_factory() as session:
                row = (
                    await session.execute(select(ExecRow).where(ExecRow.id == execution_id))
                ).scalar_one_or_none()
                return Execution.model_validate(row.data) if row else None

        async def list_executions(
            self,
            goal_id: str | None = None,
            status: str | None = None,
            limit: int = 100,
            offset: int = 0,
        ) -> list[Execution]:
            query = select(ExecRow).order_by(ExecRow.started_at.desc())
            if goal_id is not None:
                query = query.where(ExecRow.goal_id == goal_id)
            if status is not None:
                query = query.where(ExecRow.status == status)
            async with self._session_factory() as session:
                rows = (await session.execute(query.limit(limit).offset(offset))).scalars().all()
                return [Execution.model_validate(r.data) for r in rows]


def make_store(database_url: str | None) -> BaseStore:
    """Factory honouring AGENTOS_DATABASE_URL schemes."""
    url = database_url or "memory://"
    if url.startswith("memory"):
        return InMemoryStore()
    if url.startswith(("sqlite", "postgresql")):
        if not _HAS_SQLALCHEMY:
            raise RuntimeError(
                f"DATABASE_URL={url!r} requires 'pip install agentos[database]' "
                "(sqlalchemy + an async driver). Use memory:// for dependency-free mode."
            )
        return SQLStore(url)
    raise ValueError(f"Unsupported DATABASE_URL scheme: {url!r}")
