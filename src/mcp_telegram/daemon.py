"""Telegram Daemon - single process holding Telegram connection.

This daemon holds a single Telegram connection and exposes an HTTP API
for multiple MCP servers to use, solving the SQLite locking issue.
"""

import logging

from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import asyncpg
import uvicorn

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from telethon import TelegramClient
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


def get_client() -> TelegramClient:
    """Dependency to get the Telegram client."""
    if _client is None:
        raise HTTPException(status_code=503, detail="Telegram client not connected")
    return _client


@asynccontextmanager
async def daemon_lifespan(app: FastAPI):
    """Manage daemon lifecycle - connect on startup, disconnect on shutdown."""
    global _db_pool, _client, _session, _account_id

    config: DaemonConfig = app.state.config

    logger.info("Starting Telegram daemon...")

    # Connect to database
    _db_pool = await create_session_pool(config.database_url)
    logger.info("Connected to PostgreSQL")

    # Get or create account
    if config.account_id:
        _account_id = config.account_id
    else:
        # Try to find existing account
        row = await _db_pool.fetchrow(
            "SELECT id FROM telegram_accounts WHERE is_active = TRUE ORDER BY created_at LIMIT 1"
        )
        if row:
            _account_id = row["id"]
        else:
            raise RuntimeError(
                "No account found. Run 'mcp-telegram setup' first or set API_ID/API_HASH"
            )

    # Initialize session
    _session = await init_session(_db_pool, _account_id)

    # Create Telegram client
    _client = TelegramClient(
        session=_session,
        api_id=config.api_id,
        api_hash=config.api_hash,
    )

    # Connect
    await _client.connect()

    if not await _client.is_user_authorized():
        raise RuntimeError(
            "Not authorized. Run 'mcp-telegram login' first or use the setup wizard."
        )

    me = await _client.get_me()
    logger.info(f"Connected to Telegram as {me.first_name}")

    yield

    # Cleanup
    logger.info("Shutting down Telegram daemon...")
    if _client:
        await _client.disconnect()
    if _db_pool:
        await _db_pool.close()
    logger.info("Daemon stopped")


app = FastAPI(
    title="MCP Telegram Daemon",
    description="Single-process daemon for Telegram connectivity",
    version="1.0.0",
    lifespan=daemon_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Health check
@app.get("/health")
async def health():
    """Health check endpoint."""
    if _client is None or not _client.is_connected():
        raise HTTPException(status_code=503, detail="Telegram not connected")
    return {"status": "healthy", "connected": True}


# Account info
@app.get("/account")
async def get_account(client: TelegramClient = Depends(get_client)):
    """Get current account info."""
    me = await client.get_me()
    return {
        "id": me.id,
        "first_name": me.first_name,
        "last_name": me.last_name,
        "username": me.username,
        "phone": me.phone,
    }


# Messaging
@app.post("/send_message")
async def send_message(
    req: SendMessageRequest, client: TelegramClient = Depends(get_client)
):
    """Send a message to an entity."""
    try:
        entity = await parse_entity(client, req.entity)

        kwargs: dict[str, Any] = {"message": req.message}
        if req.file_path:
            kwargs["file"] = req.file_path
        if req.reply_to:
            kwargs["reply_to"] = req.reply_to

        result = await client.send_message(entity, **kwargs)
        return {"success": True, "message_id": result.id}
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/edit_message")
async def edit_message(
    req: EditMessageRequest, client: TelegramClient = Depends(get_client)
):
    """Edit a message."""
    try:
        entity = await parse_entity(client, req.entity)
        await client.edit_message(entity, req.message_id, req.message)
        return {"success": True}
    except Exception as e:
        logger.error(f"Error editing message: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/delete_message")
async def delete_message(
    req: DeleteMessageRequest, client: TelegramClient = Depends(get_client)
):
    """Delete messages."""
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
    """Get messages from an entity."""
    try:
        from datetime import datetime

        entity = await parse_entity(client, req.entity)

        kwargs: dict[str, Any] = {
            "limit": req.limit,
            "offset_id": req.offset_id,
            "reverse": req.reverse,
        }

        if req.start_date:
            kwargs["offset_date"] = datetime.fromisoformat(req.start_date)
        if req.end_date:
            # Telethon doesn't support end_date directly
            pass

        messages = await client.get_messages(entity, **kwargs)

        result = []
        for msg in messages:
            if msg:
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
    """Search for dialogs."""
    try:
        if req.global_search:
            results = await client(req.query, limit=req.limit)
        else:
            results = await client.get_dialogs(limit=req.limit)
            # Filter locally
            query = req.query.lower()
            results = [d for d in results if query in (d.name or "").lower()][: req.limit]

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


# Drafts
@app.post("/get_draft")
async def get_draft(req: SetDraftRequest, client: TelegramClient = Depends(get_client)):
    """Get draft for an entity."""
    try:
        entity = await parse_entity(client, req.entity)
        # get_drafts() returns a list, need to find the matching draft
        drafts = await client.get_drafts()
        entity_id = get_peer_id(entity)
        for draft in drafts:
            if get_peer_id(draft.entity) == entity_id:
                return {"draft": draft.text}
        return {"draft": None}
    except Exception as e:
        logger.error(f"Error getting draft: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/set_draft")
async def set_draft(req: SetDraftRequest, client: TelegramClient = Depends(get_client)):
    """Set draft for an entity."""
    try:
        entity = await parse_entity(client, req.entity)
        await client.set_draft(entity, draft=req.message)
        return {"success": True}
    except Exception as e:
        logger.error(f"Error setting draft: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Media
@app.post("/download_media")
async def download_media(
    req: DownloadMediaRequest, client: TelegramClient = Depends(get_client)
):
    """Download media from a message."""
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
    """Get message from a Telegram link."""
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
    """Run the daemon server."""
    app.state.config = config
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")
