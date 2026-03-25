"""PostgreSQL-backed Telethon session for multi-process access."""

import asyncio
import logging

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg

from telethon import types
from telethon.sessions import MemorySession

logger = logging.getLogger(__name__)


class PostgresSession(MemorySession):
    """Telethon session backend using PostgreSQL.

    Allows multiple processes to share the same Telegram session by storing
    all session data in PostgreSQL instead of SQLite.

    Inherits from MemorySession for default implementations, adds persistence.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        account_id: UUID,
    ):
        super().__init__()
        self._pool = pool
        self._account_id = account_id
        self._saved = False

        # Cache for entities (in-memory for performance)
        self._entities_cache: dict[int, tuple[int, str, str, str, int]] = {}

    async def _init_session(self) -> None:
        """Load session data from database on first access."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT dc_id, server_address, port, auth_key, takeout_id
                FROM sessions
                WHERE account_id = $1
                ORDER BY dc_id
                LIMIT 1
                """,
                self._account_id,
            )

            if row:
                self._dc_id = row["dc_id"]
                self._server_address = row["server_address"]
                self._port = row["port"]
                self._auth_key = row["auth_key"]
                self._takeout_id = row["takeout_id"]
                self._saved = True

                # Load entities cache
                entities = await conn.fetch(
                    """
                    SELECT id, hash, username, phone, name, date
                    FROM entities
                    WHERE account_id = $1
                    """,
                    self._account_id,
                )
                for e in entities:
                    self._entities_cache[e["id"]] = (
                        e["hash"],
                        e["username"] or "",
                        e["phone"] or "",
                        e["name"] or "",
                        e["date"] or 0,
                    )

    def set_dc(
        self, dc_id: int, server_address: str | None, port: int
    ) -> None:
        """Set the datacenter information."""
        super().set_dc(dc_id, server_address, port)
        self._saved = False

    @property
    def auth_key(self) -> bytes | None:
        """Get the authentication key."""
        return self._auth_key

    @auth_key.setter
    def auth_key(self, value: bytes | None) -> None:
        """Set the authentication key."""
        self._auth_key = value
        self._saved = False

    @property
    def takeout_id(self) -> int | None:
        """Get the takeout ID for data export."""
        return getattr(self, "_takeout_id", None)

    @takeout_id.setter
    def takeout_id(self, value: int | None) -> None:
        """Set the takeout ID."""
        self._takeout_id = value
        self._saved = False

    def save(self) -> None:
        """Save session data to PostgreSQL.

        Called by Telethon at various points. We use fire-and-forget
        since the Session API is synchronous.
        """
        if self._saved:
            return

        # Fire-and-forget async save with error handling
        async def _save_with_error_handling() -> None:
            try:
                await self._async_save()
                self._saved = True
            except Exception as e:
                logger.error(f"Failed to save session: {e}")
                # Don't set _saved on failure - allows retry

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_save_with_error_handling())
        except RuntimeError:
            # No running loop, sync context - create new loop
            try:
                asyncio.run(self._async_save())
                self._saved = True
            except Exception as e:
                logger.error(f"Failed to save session: {e}")

    async def _async_save(self) -> None:
        """Actual async save implementation."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO sessions (account_id, dc_id, server_address, port, auth_key, takeout_id, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, NOW())
                ON CONFLICT (account_id, dc_id) DO UPDATE SET
                    server_address = EXCLUDED.server_address,
                    port = EXCLUDED.port,
                    auth_key = EXCLUDED.auth_key,
                    takeout_id = EXCLUDED.takeout_id,
                    updated_at = NOW()
                """,
                self._account_id,
                self._dc_id,
                self._server_address,
                self._port,
                self._auth_key,
                getattr(self, "_takeout_id", None),
            )

    async def close(self) -> None:
        """Close the session and save any pending changes."""
        self.save()
        # Pool is closed externally

    def delete(self) -> None:
        """Delete the session from database."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._async_delete())
        except RuntimeError:
            asyncio.run(self._async_delete())

    async def _async_delete(self) -> None:
        """Delete session data from database."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM sessions WHERE account_id = $1",
                self._account_id,
            )
            await conn.execute(
                "DELETE FROM entities WHERE account_id = $1",
                self._account_id,
            )

    def _entity_to_row(
        self, entity: types.TypeInputPeer | types.TypeUser | types.TypeChat
    ) -> tuple[int, int] | None:
        """Extract entity ID and hash from various entity types."""
        if isinstance(entity, types.InputPeerUser):
            return (entity.user_id, entity.access_hash)
        elif isinstance(entity, types.InputPeerChat):
            return (entity.chat_id, 0)
        elif isinstance(entity, types.InputPeerChannel):
            return (entity.channel_id, entity.access_hash)
        elif isinstance(entity, types.User):
            if entity.access_hash:
                return (entity.id, entity.access_hash)
        elif isinstance(entity, types.Chat):
            return (entity.id, 0)
        elif isinstance(entity, types.Channel):
            if entity.access_hash:
                return (entity.id, entity.access_hash)
        return None

    def process_entities(
        self, tlo: Any, entities: list[Any] | None = None
    ) -> None:
        """Process and cache entities from Telegram responses."""
        if not entities:
            return

        rows = []
        for entity in entities:
            row = self._entity_to_row(entity)
            if row:
                entity_id, entity_hash = row
                username = getattr(entity, "username", None)
                phone = getattr(entity, "phone", None)
                name = self._get_entity_name(entity)
                rows.append(
                    (entity_id, entity_hash, username, phone, name)
                )
                # Update cache - use unix timestamp like original Telethon
                self._entities_cache[entity_id] = (
                    entity_hash,
                    username or "",
                    phone or "",
                    name or "",
                    int(datetime.now(timezone.utc).timestamp()),
                )

        if rows:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._async_save_entities(rows))
            except RuntimeError:
                asyncio.run(self._async_save_entities(rows))

    def _get_entity_name(self, entity: Any) -> str:
        """Extract display name from entity."""
        if hasattr(entity, "title"):
            return entity.title
        parts = []
        if hasattr(entity, "first_name") and entity.first_name:
            parts.append(entity.first_name)
        if hasattr(entity, "last_name") and entity.last_name:
            parts.append(entity.last_name)
        return " ".join(parts) if parts else ""

    async def _async_save_entities(
        self, rows: list[tuple[int, int, str | None, str | None, str]]
    ) -> None:
        """Save entities to database."""
        async with self._pool.acquire() as conn:
            for entity_id, entity_hash, username, phone, name in rows:
                await conn.execute(
                    """
                    INSERT INTO entities (account_id, id, hash, username, phone, name, date)
                    VALUES ($1, $2, $3, $4, $5, $6, EXTRACT(EPOCH FROM NOW())::BIGINT)
                    ON CONFLICT (account_id, id) DO UPDATE SET
                        hash = EXCLUDED.hash,
                        username = COALESCE(EXCLUDED.username, entities.username),
                        phone = COALESCE(EXCLUDED.phone, entities.phone),
                        name = COALESCE(EXCLUDED.name, entities.name),
                        date = EXTRACT(EPOCH FROM NOW())::BIGINT
                    """,
                    self._account_id,
                    entity_id,
                    entity_hash,
                    username,
                    phone,
                    name,
                )

    def get_input_entity(
        self, key: Any
    ) -> types.TypeInputPeer | None:
        """Get InputPeer from cached entity.

        Telegram ID ranges:
        - Users: positive IDs
        - Chats (basic groups): negative IDs from -1 to -999999999
        - Channels (supergroups/channels): negative IDs < -1000000000
        """
        entity_id = self._resolve_entity_id(key)
        if entity_id and entity_id in self._entities_cache:
            cached = self._entities_cache[entity_id]
            entity_hash = cached[0]

            if entity_id > 0:
                # User
                return types.InputPeerUser(
                    user_id=entity_id,
                    access_hash=entity_hash,
                )
            elif entity_id < -1000000000:
                # Channel or megagroup
                return types.InputPeerChannel(
                    channel_id=-1000000000 - entity_id,
                    access_hash=entity_hash,
                )
            else:
                # Basic group chat
                return types.InputPeerChat(chat_id=-entity_id)
        return None

    def _resolve_entity_id(self, key: Any) -> int | None:
        """Resolve entity ID from various key types."""
        if isinstance(key, int):
            return key
        elif isinstance(key, str):
            # Try username lookup
            for eid, cached in self._entities_cache.items():
                if cached[1] and cached[1].lower() == key.lower():
                    return eid
                if cached[2] and cached[2] == key:
                    return eid
        elif hasattr(key, "id"):
            return key.id
        return None

    # File cache methods for upload optimization
    def cache_file(
        self,
        md5_digest: bytes,
        file_size: int,
        instance: Any,
    ) -> None:
        """Cache uploaded file reference.

        Fire-and-forget pattern - saves to DB for future lookups.
        """
        if not hasattr(instance, "id") or not hasattr(instance, "hash"):
            return

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self._async_cache_file(
                    md5_digest, file_size, instance.id, instance.hash
                )
            )
        except RuntimeError:
            # No running loop - run synchronously
            asyncio.run(
                self._async_cache_file(
                    md5_digest, file_size, instance.id, instance.hash
                )
            )

    async def _async_cache_file(
        self,
        md5_digest: bytes,
        file_size: int,
        telegram_id: int,
        telegram_hash: int,
    ) -> None:
        """Save file cache to database."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO sent_files (account_id, md5_digest, file_size, type, id, hash)
                    VALUES ($1, $2, $3, 0, $4, $5)
                    ON CONFLICT (account_id, md5_digest, file_size, type) DO UPDATE SET
                        id = EXCLUDED.id,
                        hash = EXCLUDED.hash
                    """,
                    self._account_id,
                    md5_digest,
                    file_size,
                    telegram_id,
                    telegram_hash,
                )
        except Exception as e:
            logger.error(f"Failed to cache file: {e}")

    def get_file(
        self,
        md5_digest: bytes,
        file_size: int,
        cls: type,
    ) -> Any | None:
        """Get cached file reference.

        Returns cached file if available, None otherwise.
        Telethon will re-upload if None is returned.
        """
        # In async context, we need to do sync DB lookup
        # This is a known limitation of Telethon's session API
        # For now, return None to trigger re-upload (file will be cached after)
        # A proper fix would require a sync database driver or restructuring
        return None

    async def _async_get_file(
        self,
        md5_digest: bytes,
        file_size: int,
        cls: type,
    ) -> Any | None:
        """Get file from database cache."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, hash
                FROM sent_files
                WHERE account_id = $1 AND md5_digest = $2 AND file_size = $3 AND type = 0
                """,
                self._account_id,
                md5_digest,
                file_size,
            )
            if row:
                return cls(id=row["id"], hash=row["hash"])
            return None

    # Update state methods for catching up on missed updates
    def get_update_state(self, entity_id: int) -> Any | None:
        """Get update state for an entity."""
        try:
            # Fire-and-forget async call
            _ = asyncio.ensure_future(
                self._async_get_update_state(entity_id)
            )
            return None
        except RuntimeError:
            return asyncio.run(self._async_get_update_state(entity_id))

    async def _async_get_update_state(self, entity_id: int) -> Any | None:
        """Get update state from database."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT pts, qts, date, seq
                FROM update_state
                WHERE account_id = $1 AND id = $2
                """,
                self._account_id,
                entity_id,
            )
            if row:
                return types.updates.State(
                    pts=row["pts"] or 0,
                    qts=row["qts"] or 0,
                    date=row["date"] or 0,
                    seq=row["seq"] or 0,
                    unread_count=0,
                )
            return None

    def set_update_state(self, entity_id: int, state: Any) -> None:
        """Save update state for an entity."""
        if not hasattr(state, "pts"):
            return

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self._async_set_update_state(
                    entity_id,
                    state.pts,
                    getattr(state, "qts", 0),
                    getattr(state, "date", datetime.now(timezone.utc)),
                    getattr(state, "seq", 0),
                )
            )
        except RuntimeError:
            asyncio.run(
                self._async_set_update_state(
                    entity_id,
                    state.pts,
                    getattr(state, "qts", 0),
                    getattr(state, "date", datetime.now(timezone.utc)),
                    getattr(state, "seq", 0),
                )
            )

    async def _async_set_update_state(
        self,
        entity_id: int,
        pts: int,
        qts: int,
        date: datetime,
        seq: int,
    ) -> None:
        """Save update state to database."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO update_state (account_id, id, pts, qts, date, seq)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (account_id, id) DO UPDATE SET
                    pts = EXCLUDED.pts,
                    qts = EXCLUDED.qts,
                    date = EXCLUDED.date,
                    seq = EXCLUDED.seq
                """,
                self._account_id,
                entity_id,
                pts,
                qts,
                int(date.timestamp()) if date else 0,
                seq,
            )


async def create_session_pool(
    database_url: str, min_size: int = 5, max_size: int = 20
) -> asyncpg.Pool:
    """Create a connection pool for session storage."""
    return await asyncpg.create_pool(
        database_url,
        min_size=min_size,
        max_size=max_size,
        command_timeout=60,
    )


async def init_session(pool: asyncpg.Pool, account_id: UUID) -> PostgresSession:
    """Initialize a PostgresSession with the given pool."""
    session = PostgresSession(pool, account_id)
    await session._init_session()
    return session
