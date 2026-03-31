"""Telegram Daemon - single process holding Telegram connection.

This daemon holds a single Telegram connection and exposes an HTTP API
for multiple MCP servers to use, solving the SQLite locking issue.
"""

import asyncio
import json
import logging
import os
import signal

from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg
import uvicorn

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from telethon import TelegramClient, errors, hints, types  # type: ignore
from telethon.tl import functions  # type: ignore
from telethon.utils import get_peer_id

from mcp_telegram.session import PostgresSession, create_session_pool, init_session
from mcp_telegram.utils import parse_entity

logger = logging.getLogger(__name__)

# --- Log streaming for web UI ---
_log_subscribers: list[asyncio.Queue[str]] = []
_log_history: deque[str] = deque(maxlen=200)


class _QueueLogHandler(logging.Handler):
    """Pushes formatted log records to all SSE subscribers."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%H:%M:%S")
            line = json.dumps({
                "time": ts,
                "level": record.levelname,
                "message": self.format(record),
            })
            _log_history.append(line)
            for q in _log_subscribers:
                try:
                    q.put_nowait(line)
                except asyncio.QueueFull:
                    pass
        except Exception:
            pass


# Register handler on root logger so all module logs are captured
_queue_handler = _QueueLogHandler()
_queue_handler.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger().addHandler(_queue_handler)


def _handle_telethon_error(error: Exception, action: str) -> HTTPException:
    """Classify Telethon errors into human-readable HTTP errors."""
    # Telethon flood / rate limit
    if isinstance(error, errors.FloodWaitError):
        return HTTPException(
            status_code=429,
            detail=f"[Telegram] Слишком много запросов. Подождите {error.seconds} сек перед повтором.",
        )

    # Permission errors
    if isinstance(error, errors.ChatWriteForbiddenError):
        return HTTPException(
            status_code=403,
            detail="[Telegram] Нет прав писать в этот чат.",
        )
    if isinstance(error, errors.UserBannedInChannelError):
        return HTTPException(
            status_code=403,
            detail="[Telegram] Вы забанены в этом канале или группе.",
        )
    if isinstance(error, errors.ChannelPrivateError):
        return HTTPException(
            status_code=403,
            detail="[Telegram] Нет доступа к этому каналу/группе (private).",
        )
    if isinstance(error, errors.MessageDeleteForbiddenError):
        return HTTPException(
            status_code=403,
            detail="[Telegram] Нет прав удалить это сообщение.",
        )
    if isinstance(error, errors.ChatAdminRequiredError):
        return HTTPException(
            status_code=403,
            detail="[Telegram] Требуются права администратора.",
        )

    # Not found
    if isinstance(error, errors.UsernameNotOccupiedError):
        return HTTPException(
            status_code=404,
            detail="[Telegram] Пользователь/канал с таким username не найден.",
        )
    if isinstance(error, errors.PeerIdInvalidError):
        return HTTPException(
            status_code=400,
            detail=f"[Telegram] Некорректный ID сущности для действия «{action}».",
        )

    # Conflict
    if isinstance(error, errors.MessageNotModifiedError):
        return HTTPException(
            status_code=409,
            detail="[Telegram] Сообщение не изменено (контент идентичен).",
        )

    # Auth
    if isinstance(error, errors.UnauthorizedError):
        return HTTPException(
            status_code=401,
            detail="[Telegram] Сессия устарела. Необходима повторная авторизация.",
        )
    if isinstance(error, errors.AuthKeyError):
        return HTTPException(
            status_code=401,
            detail="[Telegram] Ошибка ключа авторизации. Необходима повторная авторизация.",
        )

    # Generic Telethon errors
    if isinstance(error, errors.BadRequestError):
        return HTTPException(
            status_code=400,
            detail=f"[Telegram] Некорректный запрос: {error}",
        )
    if isinstance(error, errors.ForbiddenError):
        return HTTPException(
            status_code=403,
            detail=f"[Telegram] Доступ запрещён: {error}",
        )

    # Any other Telethon error
    if isinstance(error, errors.RPCError):
        return HTTPException(
            status_code=502,
            detail=f"[Telegram] Ошибка Telegram API: {error}",
        )

    # Telethon raises plain ValueError for some cases (e.g. unknown username)
    if isinstance(error, ValueError):
        msg = str(error).lower()
        if "no user has" in msg or "username" in msg:
            return HTTPException(
                status_code=404,
                detail=f"[Telegram] Пользователь не найден: {error}",
            )
        if "no chat" in msg or "channel" in msg or "group" in msg:
            return HTTPException(
                status_code=404,
                detail=f"[Telegram] Чат/канал не найден: {error}",
            )
        return HTTPException(
            status_code=400,
            detail=f"[Telegram] Некорректный запрос: {error}",
        )

    # Non-Telethon error = daemon bug
    return HTTPException(
        status_code=500,
        detail=f"[Daemon] Внутренняя ошибка при «{action}»: {error}",
    )

# Global state
_db_pool: asyncpg.Pool | None = None
_client: TelegramClient | None = None
_session: PostgresSession | None = None
_account_id: UUID | None = None


class DaemonConfig(BaseModel):
    """Configuration for the daemon."""

    database_url: str
    api_id: int
    api_hash: str
    account_id: UUID | None = None
    host: str = "0.0.0.0"
    port: int = 8765


class SendMessageRequest(BaseModel):
    """Request body for sending a message."""

    entity: str | int
    message: str = ""
    file_path: list[str] | None = None
    reply_to: int | None = None


class EditMessageRequest(BaseModel):
    """Request body for editing a message."""

    entity: str | int
    message_id: int
    message: str


class DeleteMessageRequest(BaseModel):
    """Request body for deleting messages."""

    entity: str | int
    message_ids: list[int]


class SearchDialogsRequest(BaseModel):
    """Request body for searching dialogs."""

    query: str
    limit: int = 10
    global_search: bool = False


class GetMessagesRequest(BaseModel):
    """Request body for getting messages."""

    entity: str | int
    limit: int = 10
    start_date: str | None = None
    end_date: str | None = None
    offset_id: int = 0
    reverse: bool = False


class SetDraftRequest(BaseModel):
    """Request body for setting a draft."""

    entity: str | int
    message: str


class DownloadMediaRequest(BaseModel):
    """Request body for downloading media."""

    entity: str | int
    message_id: int
    path: str | None = None


class SendCodeRequest(BaseModel):
    """Request to send auth code."""
    phone: str


class SignInRequest(BaseModel):
    """Request to sign in with code or 2FA."""
    phone: str
    code: str | None = None
    phone_code_hash: str | None = None
    password: str | None = None


async def _run_schema_migrations(pool: asyncpg.Pool) -> None:
    """Run database migrations from the migrations directory."""
    from pathlib import Path

    migrations_dir = Path(__file__).parent.parent.parent / "migrations"
    if not migrations_dir.exists():
        logger.warning(f"Migrations directory not found at {migrations_dir}")
        return

    async with pool.acquire() as conn:
        for migration_file in sorted(migrations_dir.glob("*.sql")):
            logger.info(f"Running migration {migration_file.name}...")
            try:
                sql = migration_file.read_text()
                await conn.execute(sql)
            except Exception as e:
                logger.error(f"Failed to run migration {migration_file.name}: {e}")
                raise


@asynccontextmanager
async def daemon_lifespan(app: FastAPI):
    """Manage daemon lifecycle - connect on startup, disconnect on shutdown."""
    global _db_pool, _client, _session, _account_id

    config: DaemonConfig = app.state.config

    logger.info("Starting Telegram daemon...")

    # Connect to database
    _db_pool = await create_session_pool(config.database_url)
    logger.info("Connected to PostgreSQL")

    # Check if telegram_accounts table exists, create schema if needed
    try:
        table_exists = await _db_pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'telegram_accounts'
            )
            """
        )

        if not table_exists:
            logger.info("Database schema not found. Creating tables...")
            await _run_schema_migrations(_db_pool)
            logger.info("Database schema created successfully")
        else:
            logger.info("Database schema already exists")
    except Exception as e:
        logger.error(f"Error checking/creating schema: {e}")

    # Identify account
    if config.account_id:
        _account_id = config.account_id
    else:
        # Try to find existing account
        row = await _db_pool.fetchrow(
            """
            SELECT id FROM telegram_accounts
            WHERE is_active = TRUE ORDER BY created_at LIMIT 1
            """
        )
        if row:
            _account_id = row["id"]
        else:
            # We allow daemon to start without account for web auth
            logger.warning("No account found. Use web interface to login.")

    # Initialize session and client if we have an account
    if _account_id:
        _session = await init_session(_db_pool, _account_id)
        _client = TelegramClient(
            session=_session,
            api_id=config.api_id,
            api_hash=config.api_hash,
        )
        await _client.connect()
        
        if await _client.is_user_authorized():
            me = await _client.get_me()
            logger.info(f"Connected to Telegram as {me.first_name}")
            
            # Update account info in database
            try:
                async with _db_pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE telegram_accounts
                        SET user_id = $1, username = $2,
                            last_connected_at = NOW(), updated_at = NOW()
                        WHERE id = $3
                        """,
                        me.id,
                        getattr(me, "username", None),
                        _account_id,
                    )
            except Exception as e:
                logger.warning(f"Failed to update account info in DB: {e}")
        else:
            logger.warning("Account not authorized. Waiting for web login.")
    else:
        # Client will be initialized during auth flow
        pass

    yield

    # Cleanup
    logger.info("Shutting down Telegram daemon...")
    if _client:
        await _client.disconnect()
    if _session and hasattr(_session, "wait_for_pending_saves"):
        await _session.wait_for_pending_saves()
    if _db_pool:
        await _db_pool.close()
    logger.info("Daemon stopped")


app = FastAPI(
    title="MCP Telegram Daemon",
    lifespan=daemon_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Dependency
async def get_client() -> TelegramClient:
    global _client
    if _client is None:
        raise HTTPException(status_code=503, detail="Telegram client not initialized")
    if not await _client.is_user_authorized():
        raise HTTPException(status_code=401, detail="Unauthorized")
    return _client


# Web UI
@app.get("/", include_in_schema=False)
async def dashboard():
    """Serve the web dashboard."""
    web_dir = Path(__file__).parent / "web"
    index_file = web_dir / "index.html"
    if not index_file.exists():
        return {"error": "Web interface not found"}
    return FileResponse(index_file)


@app.get("/logo.png", include_in_schema=False)
async def logo():
    """Serve the logo."""
    logo_file = Path(__file__).parent.parent.parent / "logo.png"
    if logo_file.exists():
        return FileResponse(logo_file)
    raise HTTPException(status_code=404)


@app.get("/api/status")
async def get_status():
    """Get daemon and auth status."""
    global _client, _account_id
    
    status = {
        "connected": _client.is_connected() if _client else False,
        "authorized": False,
        "account_id": str(_account_id) if _account_id else None,
        "user": None
    }
    
    if _client and _client.is_connected() and await _client.is_user_authorized():
        status["authorized"] = True
        try:
            me = await _client.get_me()
            status["user"] = {
                "id": me.id,
                "first_name": me.first_name,
                "username": me.username,
                "phone": me.phone
            }
        except Exception:
            pass
            
    return status


@app.post("/api/auth/send-code")
async def send_code(req: SendCodeRequest):
    """Start auth flow by sending code."""
    global _client, _session, _account_id
    
    config: DaemonConfig = app.state.config
    
    try:
        # 1. Identify or create account ID
        if not _account_id:
            row = await _db_pool.fetchrow("SELECT id FROM telegram_accounts LIMIT 1")
            if row:
                _account_id = row["id"]
            else:
                from uuid import uuid4
                _account_id = uuid4()
        
        # 2. CRITICAL: Create placeholder account in DB FIRST
        # This prevents Foreign Key violations when Telethon tries to save session immediately
        async with _db_pool.acquire() as conn:
            exists = await conn.fetchval("SELECT 1 FROM telegram_accounts WHERE id = $1", _account_id)
            if not exists:
                await conn.execute(
                    """
                    INSERT INTO telegram_accounts (id, name, api_id, api_hash, phone, is_active)
                    VALUES ($1, $2, $3, $4, $5, TRUE)
                    """,
                    _account_id, "Pending Auth", config.api_id, config.api_hash, req.phone
                )

        # 3. Now initialize session and client
        if not _session:
            _session = await init_session(_db_pool, _account_id)
            
        if not _client:
            _client = TelegramClient(
                session=_session,
                api_id=config.api_id,
                api_hash=config.api_hash,
            )
            
        await _client.connect()
        result = await _client.send_code_request(req.phone)
        return {"success": True, "phone_code_hash": result.phone_code_hash}
    except Exception as e:
        logger.exception("Error sending code")
        classified = _handle_telethon_error(e, "отправка кода")
        return {"success": False, "error": classified.detail}


@app.post("/api/auth/sign-in")
async def sign_in(req: SignInRequest):
    """Complete auth flow."""
    global _client, _account_id, _db_pool
    
    if not _client:
        raise HTTPException(status_code=400, detail="Auth not started")
        
    try:
        if req.password:
            # 2FA
            await _client.sign_in(password=req.password)
        else:
            # Code
            await _client.sign_in(req.phone, req.code, phone_code_hash=req.phone_code_hash)
            
        # Auth successful! 
        me = await _client.get_me()
        
        # Ensure account exists in DB
        async with _db_pool.acquire() as conn:
            # Check if account exists
            exists = await conn.fetchval("SELECT 1 FROM telegram_accounts WHERE id = $1", _account_id)
            
            if not exists:
                await conn.execute(
                    """
                    INSERT INTO telegram_accounts (id, name, api_id, api_hash, phone, user_id, username, is_active)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE)
                    """,
                    _account_id, "Main", app.state.config.api_id, app.state.config.api_hash,
                    req.phone, me.id, me.username
                )
            else:
                await conn.execute(
                    """
                    UPDATE telegram_accounts 
                    SET user_id = $1, username = $2, phone = $3, last_connected_at = NOW()
                    WHERE id = $4
                    """,
                    me.id, me.username, req.phone, _account_id
                )
        
        return {"success": True}
    except errors.SessionPasswordNeededError:
        return {"success": False, "need_2fa": True}
    except Exception as e:
        logger.error(f"Sign in error: {e}")
        classified = _handle_telethon_error(e, "вход в аккаунт")
        return {"success": False, "error": classified.detail}


@app.post("/api/control/restart")
async def restart_service():
    """Restart the daemon by exiting (Docker will restart it)."""
    logger.info("Restart requested via Web UI")
    # Schedule exit after response
    async def shutdown():
        await asyncio.sleep(1)
        os.kill(os.getpid(), signal.SIGTERM)
        
    asyncio.create_task(shutdown())
    return {"message": "Restarting..."}


# Original Telegram API Endpoints
@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "connected": _client.is_connected() if _client else False,
        "authorized": await _client.is_user_authorized() if _client else False,
    }


@app.get("/api/logs/stream")
async def log_stream(request: Request):
    """SSE endpoint for streaming logs to the web UI."""
    queue: asyncio.Queue = asyncio.Queue()

    # Send last N history entries first
    for entry in _log_history:
        await queue.put(entry)

    _log_subscribers.append(queue)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {entry}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            if queue in _log_subscribers:
                _log_subscribers.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/send_message")
async def send_message(
    req: SendMessageRequest, client: TelegramClient = Depends(get_client)
):
    """Send a message to an entity."""
    try:
        entity = parse_entity(req.entity)
        kwargs = {}
        if req.file_path:
            kwargs["file"] = req.file_path
        if req.reply_to:
            kwargs["reply_to"] = req.reply_to

        result = await client.send_message(entity, req.message, **kwargs)
        return {"success": True, "message_id": result.id}
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        raise _handle_telethon_error(e, "отправка сообщения")


@app.post("/edit_message")
async def edit_message(
    req: EditMessageRequest, client: TelegramClient = Depends(get_client)
):
    """Edit a message."""
    try:
        entity = parse_entity(req.entity)
        await client.edit_message(entity, req.message_id, req.message)
        return {"success": True}
    except Exception as e:
        logger.error(f"Error editing message: {e}")
        raise _handle_telethon_error(e, "редактирование сообщения")


@app.post("/delete_messages")
async def delete_messages(
    req: DeleteMessageRequest, client: TelegramClient = Depends(get_client)
):
    """Delete messages."""
    try:
        entity = parse_entity(req.entity)
        await client.delete_messages(entity, req.message_ids)
        return {"success": True}
    except Exception as e:
        logger.error(f"Error deleting messages: {e}")
        raise _handle_telethon_error(e, "удаление сообщений")


@app.post("/get_messages")
async def get_messages(
    req: GetMessagesRequest, client: TelegramClient = Depends(get_client)
):
    """Get messages from an entity."""
    try:
        entity = parse_entity(req.entity)

        # Parse dates if provided
        from datetime import datetime
        start_date = datetime.fromisoformat(req.start_date) if req.start_date else None
        end_date = datetime.fromisoformat(req.end_date) if req.end_date else None

        messages = await client.get_messages(
            entity,
            limit=req.limit,
            offset_id=req.offset_id,
            reverse=req.reverse,
            offset_date=end_date,
        )

        result = []
        if messages:
            for msg in messages:
                result.append(
                    {
                        "id": msg.id,
                        "text": msg.text,
                        "date": msg.date.isoformat() if msg.date else None,
                        "from_id": msg.sender_id,
                        "out": msg.out,
                    }
                )

        return {"messages": result}
    except Exception as e:
        logger.error(f"Error getting messages: {e}")
        raise _handle_telethon_error(e, "получение сообщений")


# Search
@app.post("/search_dialogs")
async def search_dialogs(
    req: SearchDialogsRequest, client: TelegramClient = Depends(get_client)
):
    """Search for dialogs using Telegram's contacts.search API."""
    try:
        response = await client(
            functions.contacts.SearchRequest(
                q=req.query,
                limit=req.limit,
            )
        )

        assert isinstance(response, types.contacts.Found)

        # Build priority map using marked peer IDs
        priority: dict[int, int] = {}
        peers = list(response.my_results)
        if req.global_search:
            peers = peers + list(response.results)
        for i, peer in enumerate(peers):
            priority[get_peer_id(peer)] = i

        dialogs = []
        for entity in list(response.users) + list(response.chats):
            if isinstance(entity, hints.Entity):
                pid = get_peer_id(entity)
                if pid in priority:
                    name = getattr(entity, "title", None) or " ".join(
                        filter(
                            None,
                            [
                                getattr(entity, "first_name", None),
                                getattr(entity, "last_name", None),
                            ],
                        )
                    ) or None
                    dialogs.append(
                        {
                            "id": pid,
                            "name": name,
                            "username": getattr(entity, "username", None),
                            "type": type(entity).__name__,
                        }
                    )

        # Sort by search priority
        dialogs.sort(key=lambda d: priority.get(d["id"], 999))

        return {"dialogs": dialogs}
    except Exception as e:
        logger.error(f"Error searching dialogs: {e}")
        raise _handle_telethon_error(e, "поиск диалогов")


@app.post("/get_draft")
async def get_draft(req: SetDraftRequest, client: TelegramClient = Depends(get_client)):
    """Get draft for an entity."""
    try:
        entity = parse_entity(req.entity)
        draft = await client.get_drafts(entity)

        if draft and hasattr(draft, "text") and draft.text:
            return {"draft": draft.text}

        if isinstance(draft, list) and draft:
            entity_id = get_peer_id(entity)
            for d in draft:
                if get_peer_id(d.entity) == entity_id:
                    return {"draft": d.text}

        return {"draft": None}
    except Exception as e:
        logger.error(f"Error getting draft: {e}")
        raise _handle_telethon_error(e, "получение черновика")


@app.post("/set_draft")
async def set_draft(req: SetDraftRequest, client: TelegramClient = Depends(get_client)):
    """Set draft for an entity."""
    try:
        entity = parse_entity(req.entity)
        await client.set_draft(entity, req.message)
        return {"success": True}
    except Exception as e:
        logger.error(f"Error setting draft: {e}")
        raise _handle_telethon_error(e, "установка черновика")


@app.post("/download_media")
async def download_media(
    req: DownloadMediaRequest, client: TelegramClient = Depends(get_client)
):
    """Download media from a message."""
    try:
        entity = parse_entity(req.entity)
        messages = await client.get_messages(entity, ids=req.message_id)

        if not messages or not messages[0]:
            raise HTTPException(status_code=404, detail="Message not found")

        msg = messages[0]
        if not msg.media:
            raise HTTPException(status_code=400, detail="Message has no media")

        path = await client.download_media(msg, file=req.path)
        return {"success": True, "path": str(path)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading media: {e}")
        raise _handle_telethon_error(e, "скачивание медиа")


@app.post("/message_from_link")
async def message_from_link(link: str, client: TelegramClient = Depends(get_client)):
    """Get message from a Telegram link."""
    try:
        from mcp_telegram.utils import parse_telegram_url

        entity_id, message_id = parse_telegram_url(link)
        entity = parse_entity(entity_id)
        messages = await client.get_messages(entity, ids=message_id)

        if not messages or not messages[0]:
            raise HTTPException(status_code=404, detail="Message not found")

        msg = messages[0]
        return {
            "id": msg.id,
            "text": msg.text,
            "date": msg.date.isoformat() if msg.date else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting message from link: {e}")
        raise _handle_telethon_error(e, "получение сообщения по ссылке")


def run_daemon(config: DaemonConfig):
    """Run the daemon server."""
    app.state.config = config
    uvicorn.run(app, host=config.host, port=config.port)
