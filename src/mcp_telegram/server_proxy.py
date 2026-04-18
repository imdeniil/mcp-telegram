"""MCP Telegram Server - Proxy mode.

This server proxies all MCP tool calls to the Telegram daemon via HTTP.
Use this when running multiple terminals - start the daemon once,
then run this proxy in each terminal.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_telegram.proxy import get_daemon_client
from mcp_telegram.types import Dialog, DialogType, DownloadedMedia, Media, Message, Messages

logger = logging.getLogger(__name__)


def _classify_error(error: Exception, tool_name: str) -> str:
    """Classify error and add layer context if missing.

    Checks if the error message already contains a layer prefix
    ([Telegram] or [Daemon]) and passes it through.
    Otherwise, wraps with [MCP Proxy] prefix.
    """
    msg = str(error)
    if "[Telegram]" in msg or "[Daemon]" in msg:
        return msg
    return f"[MCP Proxy] {tool_name}: {msg}"


@asynccontextmanager
async def proxy_lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Lifespan manager for proxy mode - connects to daemon."""
    client = get_daemon_client()
    try:
        await client.connect()
        # Verify daemon is healthy
        health = await client.health()
        if not health.get("connected"):
            raise RuntimeError("Daemon is not connected to Telegram")
        yield
    finally:
        await client.disconnect()


mcp = FastMCP(
    "mcp-telegram",
    lifespan=proxy_lifespan,
)


def _parse_dialogs(data: dict[str, Any]) -> list[Dialog]:
    """Parse dialogs from daemon response."""
    dialogs = []
    for d in data.get("dialogs", []):
        # Map daemon response to Dialog type
        dialog_type = d.get("type", "user").lower()
        type_map = {
            "user": DialogType.USER,
            "group": DialogType.GROUP,
            "channel": DialogType.CHANNEL,
            "bot": DialogType.BOT,
            "User": DialogType.USER,
            "Group": DialogType.GROUP,
            "Channel": DialogType.CHANNEL,
        }

        dialogs.append(
            Dialog(
                id=d["id"],
                title=d.get("name") or d.get("title") or "Unknown",
                username=d.get("username"),
                phone_number=d.get("phone_number") or d.get("phone"),
                type=type_map.get(dialog_type, DialogType.USER),
                unread_messages_count=d.get("unread_messages_count", 0),
                can_send_message=d.get("can_send_message", True),
            )
        )
    return dialogs


def _parse_media(raw: dict[str, Any] | None) -> Media | None:
    """Map the daemon media dict into the Media model."""
    if not raw:
        return None
    media_id = raw.get("document_id") or raw.get("photo_id")
    if media_id is None:
        return None
    return Media(
        media_id=media_id,
        mime_type=raw.get("mime_type"),
        file_name=raw.get("filename"),
        file_size=raw.get("size"),
    )


def _parse_messages(data: dict[str, Any]) -> Messages:
    """Parse messages from daemon response."""
    messages = []
    for m in data.get("messages", []):
        messages.append(
            Message(
                message_id=m["id"],
                sender_id=m.get("from_id"),
                message=m.get("text"),
                outgoing=m.get("out", False),
                date=datetime.fromisoformat(m["date"]) if m.get("date") else None,
                media=_parse_media(m.get("media")),
                reply_to=m.get("reply_to"),
            )
        )
    return Messages(messages=messages, dialog=None)


@mcp.tool()
async def send_message(
    entity: str,
    message: str = "",
    file_path: list[str] | None = None,
    reply_to: int | None = None,
) -> str:
    """Send a message to a Telegram user, group, or channel.

    !IMPORTANT: If you are not sure about the entity, use the `search_dialogs`
    tool and ask the user to select the correct entity from the list.

    Args:
        entity: The identifier (username, phone, name, or ID) of the recipient.
        message: The text message to send.
        file_path: Optional list of file paths to send as attachments.
        reply_to: Optional message ID to reply to.

    Returns:
        Success message with the sent message ID.
    """
    client = get_daemon_client()
    try:
        result = await client.send_message(entity, message, file_path, reply_to)
        msg_ids = result.get("message_ids") or [result.get("message_id")]
        expected = result.get("files_expected", 0)
        attached = result.get("files_attached", 0)
        lines = [f"Message sent. IDs: {msg_ids}"]
        if expected > 0:
            if result.get("all_files_sent"):
                lines.append(f"Files attached: {attached}/{expected} OK")
            else:
                lines.append(f"WARNING: Files attached: {attached}/{expected} — NOT ALL ATTACHED")
            for m in result.get("messages", []):
                media = m.get("media")
                if media:
                    fname = media.get("filename") or media.get("type", "?")
                    size = media.get("size")
                    mime = media.get("mime_type")
                    parts = [str(fname)]
                    if size is not None:
                        parts.append(f"{size} bytes")
                    if mime:
                        parts.append(str(mime))
                    lines.append(f"  - msg_id={m.get('id')}: {', '.join(parts)}")
        return "\n".join(lines)
    except Exception as e:
        return _classify_error(e, "send_message")


@mcp.tool()
async def edit_message(entity: str, message_id: int, message: str) -> str:
    """Edit a previously sent message.

    Args:
        entity: The identifier of the chat where the message was sent.
        message_id: The ID of the message to edit.
        message: The new message text.

    Returns:
        Success confirmation.
    """
    client = get_daemon_client()
    try:
        await client.edit_message(entity, message_id, message)
        return "Message edited successfully"
    except Exception as e:
        return _classify_error(e, "edit_message")


@mcp.tool()
async def delete_message(entity: str, message_ids: list[int]) -> str:
    """Delete one or more messages.

    Args:
        entity: The identifier of the chat where the messages were sent.
        message_ids: List of message IDs to delete.

    Returns:
        Success confirmation.
    """
    client = get_daemon_client()
    try:
        await client.delete_message(entity, message_ids)
        return f"Deleted {len(message_ids)} message(s)"
    except Exception as e:
        return _classify_error(e, "delete_message")


@mcp.tool()
async def search_dialogs(
    query: str, limit: int = 10, global_search: bool = False
) -> list[Dialog]:
    """Search for users, groups, and channels.

    Args:
        query: The search term (name, username, or phone).
        limit: Maximum number of results to return.
        global_search: Search globally across Telegram (not just your chats).

    Returns:
        List of matching dialogs with their details.
    """
    client = get_daemon_client()
    try:
        result = await client.search_dialogs(query, limit, global_search)
        return _parse_dialogs(result)
    except Exception as e:
        return _classify_error(e, "search_dialogs")  # type: ignore[return-value]


@mcp.tool()
async def get_draft(entity: str) -> str | None:
    """Get the current draft message for a chat.

    Args:
        entity: The identifier of the chat.

    Returns:
        The draft message text, or None if no draft exists.
    """
    client = get_daemon_client()
    try:
        result = await client.get_draft(entity)
        return result.get("draft")
    except Exception as e:
        return _classify_error(e, "get_draft")


@mcp.tool()
async def set_draft(entity: str, message: str) -> str:
    """Set or clear a draft message for a chat.

    Args:
        entity: The identifier of the chat.
        message: The draft message text. Empty string clears the draft.

    Returns:
        Success confirmation.
    """
    client = get_daemon_client()
    try:
        await client.set_draft(entity, message)
        return "Draft set successfully"
    except Exception as e:
        return _classify_error(e, "set_draft")


@mcp.tool()
async def get_messages(
    entity: str,
    limit: int = 10,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    offset_id: int = 0,
    reverse: bool = False,
) -> Messages:
    """Get messages from a chat.

    Args:
        entity: The identifier of the chat.
        limit: Maximum number of messages to retrieve.
        start_date: Optional start date for filtering.
        end_date: Optional end date for filtering.
        offset_id: Offset message ID for pagination.
        reverse: If True, returns messages in chronological order.

    Returns:
        List of messages with their details.
    """
    client = get_daemon_client()
    try:
        result = await client.get_messages(
            entity,
            limit,
            start_date.isoformat() if start_date else None,
            end_date.isoformat() if end_date else None,
            offset_id,
            reverse,
        )
        return _parse_messages(result)
    except Exception as e:
        return _classify_error(e, "get_messages")  # type: ignore[return-value]


@mcp.tool()
async def media_download(
    entity: str, message_id: int, path: str | None = None
) -> DownloadedMedia:
    """Download media from a message.

    Args:
        entity: The identifier of the chat.
        message_id: The ID of the message containing media.
        path: Optional path to save the file. Auto-generated if not provided.

    Returns:
        Information about the downloaded file.
    """
    client = get_daemon_client()
    try:
        result = await client.download_media(entity, message_id, path)
        return DownloadedMedia(path=result["path"])
    except Exception as e:
        return _classify_error(e, "media_download")  # type: ignore[return-value]


@mcp.tool()
async def message_from_link(link: str) -> Message:
    """Get a message from a Telegram link.

    Args:
        link: The Telegram message link (e.g., https://t.me/...).

    Returns:
        The message details.
    """
    client = get_daemon_client()
    try:
        result = await client.message_from_link(link)
        return Message(
            id=result["id"],
            text=result.get("text"),
            date=datetime.fromisoformat(result["date"]) if result.get("date") else None,
        )
    except Exception as e:
        return _classify_error(e, "message_from_link")  # type: ignore[return-value]


def run_proxy_server():
    """Run the MCP proxy server."""
    mcp.run()
