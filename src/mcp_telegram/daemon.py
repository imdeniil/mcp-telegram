"""Telegram Daemon - single process holding Telegram connection.

This daemon holds a single Telegram connection and exposes an HTTP API
for multiple MCP servers to use, solving the SQLite locking issue.
"""

import logging
import os
import signal
import asyncio

from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID
from pathlib import Path

import asyncpg
import uvicorn

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from telethon import TelegramClient, errors
from telethon.utils import get_peer_id

from mcp_telegram.session import PostgresSession, create_session_pool, init_session
from mcp_telegram.utils import parse_entity

logger = logging.getLogger(__name__)

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
            \"\"\"
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'telegram_accounts'
            )
            \"\"\"
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
            \"\"\"
            SELECT id FROM telegram_accounts
            WHERE is_active = TRUE ORDER BY created_at LIMIT 1
            \"\"\"
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
                        \"\"\"
                        UPDATE telegram_accounts
                        SET user_id = $1, username = $2,
                            last_connected_at = NOW(), updated_at = NOW()
                        WHERE id = $3
                        \"\"\",
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
        # If no account exists yet, we'll need to create one after sign-in
        # For now, we use a temporary session or find/create account
        if not _account_id:
            # Look for ANY active account or create a default UUID
            row = await _db_pool.fetchrow("SELECT id FROM telegram_accounts LIMIT 1")
            if row:
                _account_id = row["id"]
            else:
                from uuid import uuid4
                _account_id = uuid4()
                # We'll insert it into DB after successful sign-in
        
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
        logger.error(f"Error sending code: {e}")
        return {"success": False, "error": str(e)}


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
                    \"\"\"
                    INSERT INTO telegram_accounts (id, name, api_id, api_hash, phone, user_id, username, is_active)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE)
                    \"\"\",
                    _account_id, "Main", app.state.config.api_id, app.state.config.api_hash,
                    req.phone, me.id, me.username
                )
            else:
                await conn.execute(
                    \"\"\"
                    UPDATE telegram_accounts 
                    SET user_id = $1, username = $2, phone = $3, last_connected_at = NOW()
                    WHERE id = $4
                    \"\"\",
                    me.id, me.username, req.phone, _account_id
                )
        
        return {"success": True}
    except errors.SessionPasswordNeededError:
        return {"success": False, "need_2fa": True}
    except Exception as e:
        logger.error(f"Sign in error: {e}")
        return {"success": False, "error": str(e)}


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


@app.post("/send_message")
async def send_message(
    req: SendMessageRequest, client: TelegramClient = Depends(get_client)
):
    \"\"\"Send a message to an entity.\"\"\"
    try:
        entity = await parse_entity(client, req.entity)
        kwargs = {}
        if req.file_path:
            kwargs["file"] = req.file_path
        if req.reply_to:
            kwargs["reply_to"] = req.reply_to

        result = await client.send_message(entity, req.message, **kwargs)
        return {"success": True, "message_id": result.id}
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/edit_message")
async def edit_message(
    req: EditMessageRequest, client: TelegramClient = Depends(get_client)
):
    \"\"\"Edit a message.\"\"\"
    try:
        entity = await parse_entity(client, req.entity)
        await client.edit_message(entity, req.message_id, req.message)
        return {"success": True}
    except Exception as e:
        logger.error(f"Error editing message: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/delete_messages")
async def delete_messages(
    req: DeleteMessageRequest, client: TelegramClient = Depends(get_client)
):
    \"\"\"Delete messages.\"\"\"
    try:
        entity = await parse_entity(client, req.entity)
        await client.delete_messages(entity, req.message_ids)
        return {"success": True}
    except Exception as e:
        logger.error(f"Error deleting messages: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/get_messages")
async def get_messages(
    req: GetMessagesRequest, client: TelegramClient = Depends(get_client)
):
    \"\"\"Get messages from an entity.\"\"\"
    try:
        entity = await parse_entity(client, req.entity)
        
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
        raise HTTPException(status_code=500, detail=str(e))


# Search
@app.post("/search_dialogs")
async def search_dialogs(
    req: SearchDialogsRequest, client: TelegramClient = Depends(get_client)
):
    \"\"\"Search for dialogs.\"\"\"
    try:
        if req.global_search:
            results = await client(req.query, limit=req.limit)
        else:
            results = await client.get_dialogs(limit=req.limit)
            # Filter locally
            query = req.query.lower()
            results = [
                d for d in results if query in (d.name or "").lower()
            ][: req.limit]

        dialogs = []
        for dialog in results:
            if hasattr(dialog, "entity"):
                entity = dialog.entity
                dialogs.append(
                    {
                        "id": entity.id,
                        "name": getattr(dialog, "name", None),
                        "username": getattr(entity, "username", None),
                        "type": type(entity).__name__,
                    }
                )

        return {"dialogs": dialogs}
    except Exception as e:
        logger.error(f"Error searching dialogs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/get_draft")
async def get_draft(req: SetDraftRequest, client: TelegramClient = Depends(get_client)):
    \"\"\"Get draft for an entity.\"\"\"
    try:
        entity = await parse_entity(client, req.entity)
        # get_drafts(entity) returns a single custom.Draft object for that entity
        # or a list if entity is None. We pass entity to get exactly what we need.
        draft = await client.get_drafts(entity)

        if draft and hasattr(draft, "text") and draft.text:
            return {"draft": draft.text}

        # Fallback: if it returned a list, find the matching one
        if isinstance(draft, list) and draft:
            entity_id = get_peer_id(entity)
            for d in draft:
                if get_peer_id(d.entity) == entity_id:
                    return {"draft": d.text}

        return {"draft": None}
    except Exception as e:
        logger.error(f"Error getting draft: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/set_draft")
async def set_draft(req: SetDraftRequest, client: TelegramClient = Depends(get_client)):
    \"\"\"Set draft for an entity.\"\"\"
    try:
        entity = await parse_entity(client, req.entity)
        await client.set_draft(entity, req.message)
        return {"success": True}
    except Exception as e:
        logger.error(f"Error setting draft: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/download_media")
async def download_media(
    req: DownloadMediaRequest, client: TelegramClient = Depends(get_client)
):
    \"\"\"Download media from a message.\"\"\"
    try:
        entity = await parse_entity(client, req.entity)
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
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/message_from_link")
async def message_from_link(link: str, client: TelegramClient = Depends(get_client)):
    \"\"\"Get message from a Telegram link.\"\"\"
    try:
        from mcp_telegram.utils import parse_telegram_url

        entity_id, message_id = parse_telegram_url(link)
        entity = await parse_entity(client, entity_id)
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
        raise HTTPException(status_code=500, detail=str(e))


def run_daemon(config: DaemonConfig):
    \"\"\"Run the daemon server.\"\"\"
    app.state.config = config
    uvicorn.run(app, host=config.host, port=config.port)
