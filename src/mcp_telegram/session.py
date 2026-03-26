"""PostgreSQL-backed Telethon session for multi-process access."""

import asyncio
import logging

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg

from telethon import types, utils
from telethon.crypto import AuthKey
from telethon.sessions import MemorySession

logger = logging.getLogger(__name__)


class PostgresSession(MemorySession):
    """Telethon session backend using PostgreSQL."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        account_id: UUID,
    ):
        super().__init__()
        self._pool = pool
        self._account_id = account_id
        self._saved = False
        self._file_cache: dict[tuple[bytes, int], dict[str, int]] = {}
        self._pending_saves: set[asyncio.Task] = set()
        self._save_lock = asyncio.Lock()

    async def _init_session(self) -> None:
        """Load session data from database on first access."""
        try:
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

                    # Load entities into MemorySession set
                    entities = await conn.fetch(
                        "SELECT id, hash, username, phone, name FROM entities WHERE account_id = $1",
                        self._account_id,
                    )
                    for e in entities:
                        # (id, hash, username, phone, name)
                        self._entities.add((e["id"], e["hash"], e["username"], e["phone"], e["name"]))
        except Exception as e:
            logger.error(f"Failed to initialize session from DB: {e}")

    @property
    def auth_key(self) -> AuthKey | None:
        return AuthKey(self._auth_key) if self._auth_key else None

    @auth_key.setter
    def auth_key(self, value: Any | None) -> None:
        if hasattr(value, 'key'):
            value = value.key
        if self._auth_key != value:
            self._auth_key = value
            self._saved = False
            self.save()

    def save(self) -> None:
        if self._saved or self._pool.is_closing():
            return

        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._async_save_tracked())
            self._pending_saves.add(task)
            task.add_done_callback(self._pending_saves.discard)
        except RuntimeError:
            pass
        self._saved = True

    async def _async_save_tracked(self) -> None:
        try:
            async with self._save_lock:
                await self._async_save()
        except Exception:
            self._saved = False

    async def _async_save(self) -> None:
        if self._pool.is_closing():
            return
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
                self._account_id, self._dc_id, self._server_address, self._port, self._auth_key, self._takeout_id
            )

    def process_entities(self, tlo: Any, entities: list[Any] | None = None) -> None:
        # Crucial for Telethon stability
        super().process_entities(tlo)
        
        # Collect entities for DB persistence
        if not entities:
            return
            
        rows = []
        for entity in entities:
            if entity is None:
                continue
            try:
                # Use Telethon's own logic to get ID and Hash
                eid = utils.get_peer_id(entity)
                # Not all entities have access_hash (e.g. Chat)
                ehash = getattr(entity, "access_hash", 0) or 0
                rows.append((
                    eid, ehash, 
                    getattr(entity, "username", None), 
                    getattr(entity, "phone", None), 
                    utils.get_display_name(entity)
                ))
            except Exception:
                continue
                
        if rows and not self._pool.is_closing():
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(self._async_save_entities(rows))
                self._pending_saves.add(task)
                task.add_done_callback(self._pending_saves.discard)
            except RuntimeError:
                pass

    async def _async_save_entities(self, rows: list[tuple]) -> None:
        if self._pool.is_closing():
            return
        async with self._pool.acquire() as conn:
            for eid, ehash, uname, phone, name in rows:
                await conn.execute(
                    """
                    INSERT INTO entities (account_id, id, hash, username, phone, name, date)
                    VALUES ($1, $2, $3, $4, $5, $6, EXTRACT(EPOCH FROM NOW())::BIGINT)
                    ON CONFLICT (account_id, id) DO UPDATE SET
                        hash = EXCLUDED.hash,
                        username = COALESCE(EXCLUDED.username, entities.username),
                        phone = COALESCE(EXCLUDED.phone, entities.phone),
                        name = COALESCE(EXCLUDED.name, entities.name)
                    """,
                    self._account_id, eid, ehash, uname, phone, name
                )

    async def wait_for_pending_saves(self, timeout: float = 2.0) -> None:
        if self._pending_saves:
            tasks = [t for t in self._pending_saves if not t.done()]
            if tasks:
                await asyncio.wait(tasks, timeout=timeout)

    def cache_file(self, md5: bytes, size: int, inst: Any) -> None:
        if hasattr(inst, "id"): 
            self._file_cache[(md5, size)] = {"id": inst.id, "hash": inst.hash}

    def get_file(self, md5: bytes, size: int, cls: type) -> Any | None:
        cached = self._file_cache.get((md5, size))
        return cls(id=cached["id"], hash=cached["hash"]) if cached else None


async def create_session_pool(url: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(url, min_size=1, max_size=10)


async def init_session(pool: asyncpg.Pool, account_id: UUID) -> PostgresSession:
    session = PostgresSession(pool, account_id)
    await session._init_session()
    return session
