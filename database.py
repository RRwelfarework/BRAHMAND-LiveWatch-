import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


metadata = MetaData()
events_table = Table(
    "live_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source", String(40), nullable=False),
    Column("external_id", String(120), nullable=False),
    Column("title", String(500), nullable=False),
    Column("place", String(500), nullable=False, default=""),
    Column("event_time", String(80)),
    Column("severity", String(40), nullable=False, default="unclassified"),
    Column("payload", Text, nullable=False),
    Column("first_seen_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("source", "external_id", name="uq_live_events_source_external_id"),
)


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    if url.startswith("sqlite:///") and not url.startswith("sqlite+aiosqlite:///"):
        return "sqlite+aiosqlite:///" + url.removeprefix("sqlite:///")
    return url


def event_time(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        timestamp = value / 1000 if abs(value) > 100_000_000_000 else value
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    if isinstance(value, str) and value:
        return value
    return None


class EventStore:
    def __init__(self, database_url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(
            normalize_database_url(database_url),
            pool_pre_ping=True,
        )

    async def initialize(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    async def ping(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def save_events(self, source: str, events: list[dict[str, Any]]) -> int:
        now = datetime.now(timezone.utc)
        records = []
        for event in events:
            external_id = event.get("id")
            if external_id is None:
                continue
            categories = event.get("categories")
            place = event.get("place") or (
                ", ".join(str(category) for category in categories)
                if isinstance(categories, list)
                else ""
            )
            records.append(
                {
                    "source": source,
                    "external_id": str(external_id),
                    "title": str(event.get("title") or "Natural event"),
                    "place": str(place),
                    "event_time": event_time(event.get("time")),
                    "severity": str(event.get("severity") or "unclassified"),
                    "payload": json.dumps(event, ensure_ascii=False),
                    "first_seen_at": now,
                    "updated_at": now,
                }
            )
        if not records:
            return 0

        dialect = self.engine.dialect.name
        if dialect == "postgresql":
            insert = postgres_insert
        elif dialect == "sqlite":
            insert = sqlite_insert
        else:
            raise RuntimeError(f"Unsupported database dialect: {dialect}")

        async with self.engine.begin() as connection:
            for start in range(0, len(records), 250):
                statement = insert(events_table).values(records[start : start + 250])
                statement = statement.on_conflict_do_update(
                    index_elements=[events_table.c.source, events_table.c.external_id],
                    set_={
                        "title": statement.excluded.title,
                        "place": statement.excluded.place,
                        "event_time": statement.excluded.event_time,
                        "severity": statement.excluded.severity,
                        "payload": statement.excluded.payload,
                        "updated_at": statement.excluded.updated_at,
                    },
                )
                await connection.execute(statement)
        return len(records)

    async def get_history(self, limit: int) -> list[dict[str, Any]]:
        statement = (
            select(events_table)
            .order_by(
                events_table.c.event_time.desc().nullslast(),
                events_table.c.first_seen_at.desc(),
            )
            .limit(limit)
        )
        async with self.engine.connect() as connection:
            result = await connection.execute(statement)

        prefix = {"USGS": "usgs", "NASA EONET": "eonet"}
        history = []
        for row in result.mappings():
            history.append(
                {
                    "id": f"{prefix.get(row['source'], row['source'].lower())}:{row['external_id']}",
                    "title": row["title"],
                    "place": row["place"],
                    "time": row["event_time"],
                    "severity": row["severity"],
                    "source": row["source"],
                    "first_seen_at": row["first_seen_at"].isoformat(),
                    "data": json.loads(row["payload"]),
                }
            )
        return history
