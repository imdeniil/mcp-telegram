"""MCP Telegram Server - Proxy mode.

This server proxies all MCP tool calls to the Telegram daemon via HTTP.
Use this when running multiple terminals - start the daemon once,
then run this proxy in each terminal.
"""

import logging
import os

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_telegram.proxy import get_daemon_client
from mcp_telegram.transport import run_mcp_server
from mcp_telegram.types import (
    ChatMessages,
    DATE_INPUT_GUIDE,
    Dialog,
    DialogType,
    DownloadedMedia,
    ExportResult,
    Folder,
    Media,
    Message,
    Messages,
)

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


@mcp.resource(
    "docs://date-formats",
    name="date-formats",
    description="How to enter dates for date-filtered tools.",
)
def date_formats() -> str:
    """Date input guide for date-filtered tools."""
    return DATE_INPUT_GUIDE


def _parse_dialog(d: dict[str, Any]) -> Dialog:
    """Parse a single dialog dict from a daemon response."""
    dialog_type = str(d.get("type", "user")).lower()
    type_map = {
        "user": DialogType.USER,
        "group": DialogType.GROUP,
        "channel": DialogType.CHANNEL,
        "bot": DialogType.BOT,
    }
    return Dialog(
        id=d["id"],
        title=d.get("name") or d.get("title") or "Unknown",
        username=d.get("username"),
        phone_number=d.get("phone_number") or d.get("phone"),
        type=type_map.get(dialog_type, DialogType.USER),
        unread_messages_count=d.get("unread_messages_count", 0),
        can_send_message=d.get("can_send_message", True),
    )


def _parse_dialogs(data: dict[str, Any]) -> list[Dialog]:
    """Parse dialogs from daemon response."""
    return [_parse_dialog(d) for d in data.get("dialogs", [])]


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
    return Messages(
        messages=messages,
        dialog=None,
        next_offset_id=data.get("next_offset_id"),
        has_more=bool(data.get("has_more", False)),
    )


def _parse_export(data: dict[str, Any]) -> ExportResult:
    """Parse a cross-chat export response from the daemon."""
    results: list[ChatMessages] = []
    for entry in data.get("results", []):
        dialog = _parse_dialog(entry.get("dialog") or {})
        raw_msgs = entry.get("messages", [])
        messages = [
            Message(
                message_id=m["id"],
                sender_id=m.get("from_id"),
                message=m.get("text"),
                outgoing=m.get("out", False),
                date=datetime.fromisoformat(m["date"]) if m.get("date") else None,
                media=_parse_media(m.get("media")),
                reply_to=m.get("reply_to"),
            )
            for m in raw_msgs
        ]
        results.append(ChatMessages(dialog=dialog, messages=messages))
    return ExportResult(
        results=results,
        chats_processed=data.get("chats_processed", len(results)),
        truncated=bool(data.get("truncated", False)),
    )


def _parse_folder(d: dict[str, Any]) -> Folder:
    """Parse a folder dict from a daemon response."""
    return Folder(
        id=d["id"],
        title=d.get("title", ""),
        emoticon=d.get("emoticon"),
        contacts=bool(d.get("contacts", False)),
        non_contacts=bool(d.get("non_contacts", False)),
        groups=bool(d.get("groups", False)),
        broadcasts=bool(d.get("broadcasts", False)),
        bots=bool(d.get("bots", False)),
        exclude_muted=bool(d.get("exclude_muted", False)),
        exclude_read=bool(d.get("exclude_read", False)),
        exclude_archived=bool(d.get("exclude_archived", False)),
        include_peer_ids=list(d.get("include_peer_ids", [])),
        exclude_peer_ids=list(d.get("exclude_peer_ids", [])),
        pinned_peer_ids=list(d.get("pinned_peer_ids", [])),
        is_chatlist=bool(d.get("is_chatlist", False)),
        is_default=bool(d.get("is_default", False)),
    )


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
async def export_messages(
    entities: list[str | int] | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    per_chat_limit: int = 100,
    max_chats: int = 30,
) -> ExportResult:
    """Export messages across multiple chats for a date window.

    Provide `entities` to export specific chats, or leave it None to export the
    most recent chats (up to `max_chats`). Each chat returns up to
    `per_chat_limit` messages; page individual chats further via `get_messages`
    with the `offset_id` cursor.

    Args:
        entities: Chat identifiers to export. None = recent dialogs (max_chats).
        start_date: Start of the window (required for a bounded export).
        end_date: End of the window. Defaults to now.
        per_chat_limit: Max messages per chat (1..500). Default 100.
        max_chats: Max chats when entities is None (1..100). Default 30.

    Returns:
        Per-chat message batches with a truncation flag.
    """
    if start_date is None:
        return _classify_error(ValueError("start_date is required"), "export_messages")  # type: ignore[return-value]
    client = get_daemon_client()
    try:
        result = await client.export_messages(
            entities,
            start_date.isoformat(),
            end_date.isoformat() if end_date else None,
            per_chat_limit,
            max_chats,
        )
        return _parse_export(result)
    except Exception as e:
        return _classify_error(e, "export_messages")  # type: ignore[return-value]


@mcp.tool()
async def get_folders() -> list[Folder]:
    """Get all chat folders (dialog filters)."""
    client = get_daemon_client()
    try:
        result = await client.get_folders()
        return [_parse_folder(f) for f in result.get("folders", [])]
    except Exception as e:
        return _classify_error(e, "get_folders")  # type: ignore[return-value]


@mcp.tool()
async def get_folder_chats(folder_id: int, limit: int = 100) -> list[Dialog]:
    """Get the chats that belong to a specific folder.

    Args:
        folder_id: The folder ID (use get_folders to find it). 0 = all
            non-archived, 1 = archived.
        limit: Max number of chats to return. Default 100.

    Returns:
        The dialogs contained in the folder.
    """
    client = get_daemon_client()
    try:
        result = await client.get_folder_chats(folder_id, limit)
        return _parse_dialogs(result)
    except Exception as e:
        return _classify_error(e, "get_folder_chats")  # type: ignore[return-value]


@mcp.tool()
async def create_folder(
    title: str,
    emoticon: str | None = None,
    include_entities: list[str | int] | None = None,
    exclude_entities: list[str | int] | None = None,
    contacts: bool = False,
    non_contacts: bool = False,
    groups: bool = False,
    broadcasts: bool = False,
    bots: bool = False,
    exclude_muted: bool = False,
    exclude_read: bool = False,
    exclude_archived: bool = False,
) -> Folder:
    """Create a new chat folder.

    Args:
        title: The folder title.
        emoticon: Optional emoji icon.
        include_entities: Entities to explicitly include.
        exclude_entities: Entities to explicitly exclude.
        contacts/non_contacts/groups/broadcasts/bots: Auto-include criteria.
        exclude_muted/exclude_read/exclude_archived: Auto-exclude criteria.

    Returns:
        The created folder.
    """
    client = get_daemon_client()
    try:
        result = await client.create_folder(
            title,
            emoticon=emoticon,
            include_entities=include_entities,
            exclude_entities=exclude_entities,
            contacts=contacts,
            non_contacts=non_contacts,
            groups=groups,
            broadcasts=broadcasts,
            bots=bots,
            exclude_muted=exclude_muted,
            exclude_read=exclude_read,
            exclude_archived=exclude_archived,
        )
        return _parse_folder(result)
    except Exception as e:
        return _classify_error(e, "create_folder")  # type: ignore[return-value]


@mcp.tool()
async def update_folder(
    folder_id: int,
    title: str | None = None,
    emoticon: str | None = None,
    add_entities: list[str | int] | None = None,
    remove_entities: list[str | int] | None = None,
) -> Folder:
    """Update an existing chat folder (rename / add / remove chats).

    Args:
        folder_id: The folder ID to update.
        title: New title.
        emoticon: New emoji icon (empty string clears it).
        add_entities: Entities to add.
        remove_entities: Entities to remove.

    Returns:
        The updated folder.
    """
    client = get_daemon_client()
    try:
        result = await client.update_folder(
            folder_id,
            title=title,
            emoticon=emoticon,
            add_entities=add_entities,
            remove_entities=remove_entities,
        )
        return _parse_folder(result)
    except Exception as e:
        return _classify_error(e, "update_folder")  # type: ignore[return-value]


@mcp.tool()
async def delete_folder(folder_id: int) -> str:
    """Delete a chat folder.

    Args:
        folder_id: The folder ID to delete (must be a user folder, ID >= 2).

    Returns:
        Success confirmation.
    """
    client = get_daemon_client()
    try:
        await client.delete_folder(folder_id)
        return f"Folder {folder_id} deleted"
    except Exception as e:
        return _classify_error(e, "delete_folder")


@mcp.tool()
async def reorder_folders(folder_ids: list[int]) -> str:
    """Reorder chat folders.

    Args:
        folder_ids: The desired order of folder IDs.

    Returns:
        Success confirmation.
    """
    client = get_daemon_client()
    try:
        await client.reorder_folders(folder_ids)
        return f"Folders reordered: {folder_ids}"
    except Exception as e:
        return _classify_error(e, "reorder_folders")


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


def run_proxy_server(
    transport: str = "stdio",
    host: str = "0.0.0.0",
    port: int = 8766,
    auth_token: str | None = None,
) -> None:
    """Run the MCP proxy server (daemon-backed) on the given transport.

    In HTTP mode, also reverse-proxies the daemon's web dashboard/API
    (``DAEMON_URL``) so the public gateway serves both MCP and the dashboard.
    """
    run_mcp_server(
        mcp,
        transport=transport,
        host=host,
        port=port,
        auth_token=auth_token,
        daemon_url=os.environ.get("DAEMON_URL"),
    )
