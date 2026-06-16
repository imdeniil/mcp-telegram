"""MCP Telegram Server."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from mcp_telegram.telegram import Telegram
from mcp_telegram.transport import run_mcp_server
from mcp_telegram.types import (
    DATE_INPUT_GUIDE,
    Dialog,
    DownloadedMedia,
    ExportResult,
    Folder,
    Message,
    Messages,
)
from mcp_telegram.utils import parse_entity


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Lifespan manager for the app.

    This will connect to Telegram on startup and disconnect on shutdown.
    """
    try:
        tg.create_client()
        await tg.client.connect()
        yield
    finally:
        await tg.client.disconnect()  # type: ignore


tg = Telegram()
mcp = FastMCP(
    "mcp-telegram",
    lifespan=app_lifespan,
)


@mcp.resource(
    "docs://date-formats",
    name="date-formats",
    description="How to enter dates for date-filtered tools.",
)
def date_formats() -> str:
    """Date input guide for date-filtered tools."""
    return DATE_INPUT_GUIDE


@mcp.tool()
async def send_message(
    entity: str,
    message: str = "",
    file_path: list[str] | None = None,
    reply_to: int | None = None,
) -> str:
    """Send a message to a Telegram user, group, or channel.

    It allows sending text messages to any Telegram entity identified by `entity`.

    !IMPORTANT: If you are not sure about the entity, use the `search_dialogs`
    tool and ask the user to select the correct entity from the list.

    Args:
        entity (`str`): The identifier of where to send the message.
            This can be a Telegram chat ID, a username, a phone number
            (in format '+1234567890'), or a group/channel username. The special
            value "me" can be used to send a message to yourself.

        message (`str`, optional): The text message to be sent.
            The message supports Markdown formatting including **bold**, __italic__,
            `monospace`, and [URL](links). The maximum length for a message is 35,000
            bytes or 4,096 characters.

        file_path (`list[str]`, optional): The list of paths to the files to be sent.

        reply_to (`int`, optional): The message ID to reply to.

    Returns:
        `str`:
            A success message if sent, or an error message if failed.
    """

    _entity = parse_entity(entity)

    await tg.send_message(
        _entity,
        message,
        file_path=file_path,
        reply_to=reply_to,
    )

    return f"Message sent to {entity}"


@mcp.tool()
async def edit_message(entity: str, message_id: int, message: str) -> str:
    """Edit a message from a specific entity.

    Edits a message from a specific entity.

    !IMPORTANT: If the entity is not found, it will return an error message.
    If you are not sure about the entity, use the `search_dialogs`
    tool and ask the user to select the correct entity from the list.
    If you are not sure about the message ID, use the `get_messages`
    tool to get the message ID.

    Args:
        entity (`str`): The identifier of the entity.
        message_id (`int`): The ID of the message to edit.
        message (`str`): The message to edit the message to.

    Returns:
        `str`:
            A success message if edited, or an error message if failed.
    """

    _entity = parse_entity(entity)

    await tg.edit_message(_entity, message_id, message)

    return f"Message edited in {entity}"


@mcp.tool()
async def delete_message(entity: str, message_ids: list[int]) -> str:
    """Delete messages from a specific entity.

    Deletes messages from a specific entity.

    !IMPORTANT: If the entity is not found, it will return an error message.
    If you are not sure about the entity, use the `search_dialogs`
    tool and ask the user to select the correct entity from the list.
    If you are not sure about the message IDs, use the `get_messages`
    tool to get the message IDs.

    Args:
        entity (`str`): The identifier of the entity.
        message_ids (`list[int]`): The IDs of the messages to delete.

    Returns:
        `str`:
            A success message if deleted, or an error message if failed.
    """

    _entity = parse_entity(entity)

    await tg.delete_message(_entity, message_ids)

    return f"Messages deleted from {entity}"


@mcp.tool()
async def search_dialogs(
    query: str, limit: int = 10, global_search: bool = False
) -> list[Dialog]:
    """Search for users, groups, and channels.

    Retrieves users, groups, and channels and filters them based
    on the provided query. The query performs a case-insensitive search.

    !IMPORTANT: If the query doesn't return the correct results, it means that
    the query is not specific enough. Try to be more specific with the query or
    use a different query.

    Args:
        query (`str`): A query string to filter the dialogs.
            The search will return only dialogs where the query string is
            found within the dialog's title or username.

        limit (`int`, optional): The maximum number of dialogs to return.
            Defaults to 10. The limit must be greater than 0.

        global_search (`bool`, optional): Whether to perform a global search.
            Defaults to False.

    Returns:
        `list[Dialog]`: A list of dialogs that match the query if successful,
            or an error message if request failed.
    """

    return await tg.search_dialogs(query, limit, global_search)


@mcp.tool()
async def get_draft(entity: str) -> str:
    """Get the draft message for a specific entity.

    Finds the draft message for an entity specified by username, chat_id,
    phone number, or 'me'.

    !IMPORTANT: If the entity is not found, it will return an error message.
    If you are not sure about the entity, use the `search_dialogs`
    tool and ask the user to select the correct entity from the list.

    Args:
        entity (`str`):
            The identifier of the entity to get the draft message for.
            This can be a Telegram chat ID, a username, a phone number, or 'me'.

    Returns:
        `str`:
            The draft message (empty string if no draft) for the specific entity
            or an error message if request failed.
    """

    _entity = parse_entity(entity)

    return await tg.get_draft(_entity)


@mcp.tool()
async def set_draft(entity: str, message: str) -> str:
    """Set a draft message for a specific entity.

    Sets a draft message for an entity specified by username, chat_id,
    phone number, or 'me'.

    !IMPORTANT: If the entity is not found, it will return an error message.
    If you are not sure about the entity, use the `search_dialogs`
    tool and ask the user to select the correct entity from the list.

    Args:
        entity (`str`):
            The identifier of the entity to save the draft message for.
            This can be a Telegram chat ID, a username, a phone number, or 'me'.

        message (`str`):
            The message to save as a draft.

    Returns:
        `str`:
            A success message if saved, or an error message if failed.
    """

    _entity = parse_entity(entity)

    await tg.set_draft(_entity, message)

    return f"Draft saved for {_entity}"


@mcp.tool()
async def get_messages(
    entity: str,
    limit: int = 10,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    unread: bool = False,
    mark_as_read: bool = False,
    offset_id: int | None = None,
) -> Messages:
    """Get messages from a specific entity.

    Retrieves messages from an entity specified by username, chat_id,
    phone number, or 'me'.

    !IMPORTANT: If the entity is not found, it will return an error message.
    If you are not sure about the entity, use the `search_dialogs`
    tool and ask the user to select the correct entity from the list.

    Args:
        entity (`str`):
            The identifier of the entity to get messages from.
            This can be a Telegram chat ID, a username, a phone number, or 'me'.

        limit (`int`, optional):
            The maximum number of messages to retrieve.
            Defaults to 10.

        start_date (`datetime`, optional):
            The start date of the messages to retrieve.

        end_date (`datetime`, optional):
            The end date of the messages to retrieve.

        unread (`bool`, optional):
            Whether to get only unread messages.
            Defaults to False.

        mark_as_read (`bool`, optional):
            Whether to mark the messages as read.
            Defaults to False.

        offset_id (`int`, optional):
            Pagination cursor: fetch messages older than this message ID.
            Use the `next_offset_id` from a previous result to fetch the next
            page. Defaults to None (newest / `end_date`).

    Returns:
        `Messages`:
            A page of messages from the entity and the dialog the messages
            belong to, plus `next_offset_id` and `has_more` for pagination.
    """

    _entity = parse_entity(entity)

    return await tg.get_messages(
        _entity,
        limit,
        start_date,
        end_date,
        unread,
        mark_as_read,
        offset_id,
    )


@mcp.tool()
async def export_messages(
    entities: list[str | int] | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    per_chat_limit: int = 100,
    max_chats: int = 30,
) -> ExportResult:
    """Export messages across multiple chats for a date window.

    Retrieves messages from several chats at once within a `[start_date,
    end_date]` window. Provide `entities` to export specific chats, or leave it
    None to export the most recent chats (up to `max_chats`). Each chat returns
    up to `per_chat_limit` messages; page individual chats further via
    `get_messages` with its `offset_id` cursor.

    Args:
        entities (`list[str]`, optional):
            Chat identifiers (IDs, usernames, phone numbers) to export. If None,
            the account's recent dialogs (up to `max_chats`) are exported.

        start_date (`datetime`):
            Start of the export window. Required.

        end_date (`datetime`, optional):
            End of the export window. Defaults to now.

        per_chat_limit (`int`, optional):
            Maximum messages per chat. Defaults to 100, clamped to [1, 500].

        max_chats (`int`, optional):
            Maximum chats when `entities` is None. Defaults to 30, clamped to
            [1, 100].

    Returns:
        `ExportResult`:
            Per-chat message batches (`results`), the number of chats processed,
            and `truncated` (True if any chat hit `per_chat_limit`).
    """

    return await tg.export_messages(
        entities,
        start_date,
        end_date,
        per_chat_limit,
        max_chats,
    )


@mcp.tool()
async def media_download(
    entity: str, message_id: int, path: str | None = None
) -> DownloadedMedia:
    """Download media from a specific message to a unique local file.

    Retrieves media from an entity specified by username, chat_id,
    phone number, or 'me' and saves it to a local directory with a unique name.

    !IMPORTANT: If the entity is not found, it will return an error message.
    If you are not sure about the entity, use the `search_dialogs`
    tool and ask the user to select the correct entity from the list.

    Args:
        entity (`str`):
            The identifier of the entity where the message exists.
            This can be a Telegram chat ID, a username, a phone number, or 'me'.

        message_id (`int`):
            The ID of the message containing the media to download.

        path (`str`, optional):
            The path to save the downloaded media.
            Defaults to a Path corresponding to `XDG_STATE_HOME`.

    Returns:
        `DownloadedMedia`:
            An object containing the absolute path and media details
            of the downloaded file if successful or an error message.
    """
    _entity = parse_entity(entity)

    return await tg.download_media(_entity, message_id, path)


@mcp.tool()
async def message_from_link(link: str) -> Message:
    """Get a message from a link.

    Retrieves a message from a link.

    !IMPORTANT: If the link is not a valid Telegram message link, or the account
    is not authorized to access the message, it will return an error message.

    Args:
        link (`str`): The link to the message.

    Returns:
        `Message`: The message from the link if successful, or an error message.
    """

    return await tg.message_from_link(link)


@mcp.tool()
async def get_folders() -> list[Folder]:
    """Get all chat folders (dialog filters).

    Returns the list of folders configured by the user, including the default
    'All Chats' folder if present. Each folder reports its ID, title, emoji
    icon, inclusion/exclusion criteria, and the explicit peer IDs it contains.

    Returns:
        `list[Folder]`: The list of chat folders.
    """
    return await tg.get_folders()


@mcp.tool()
async def get_folder_chats(folder_id: int, limit: int = 100) -> list[Dialog]:
    """Get the chats that belong to a specific folder.

    Lists the dialogs contained in a folder identified by `folder_id`. Use
    `get_folders` first to discover the available folder IDs. The special IDs
    `0` (all non-archived chats) and `1` (archived chats) are also accepted.

    Args:
        folder_id (`int`): The folder ID to list chats from.
        limit (`int`, optional): The maximum number of chats to return.
            Defaults to 100. Must be greater than 0.

    Returns:
        `list[Dialog]`: The dialogs contained in the folder.
    """
    return await tg.get_folder_dialogs(folder_id, limit)


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

    At least one inclusion source should be provided: explicit `include_entities`
    or one of the criteria flags (`contacts`, `groups`, `broadcasts`, `bots`,
    `non_contacts`), otherwise the folder will be empty.

    !IMPORTANT: This creates a folder on the user's Telegram account. Confirm
    the parameters with the user before calling.

    Args:
        title (`str`): The title of the folder.
        emoticon (`str`, optional): An emoji icon for the folder.
        include_entities (`list[str]`, optional): Entities (chat IDs, usernames,
            phone numbers) to explicitly include.
        exclude_entities (`list[str]`, optional): Entities to explicitly exclude.
        contacts (`bool`, optional): Auto-include contacts.
        non_contacts (`bool`, optional): Auto-include non-contacts.
        groups (`bool`, optional): Auto-include groups.
        broadcasts (`bool`, optional): Auto-include channels.
        bots (`bool`, optional): Auto-include bots.
        exclude_muted (`bool`, optional): Exclude muted chats.
        exclude_read (`bool`, optional): Exclude read chats.
        exclude_archived (`bool`, optional): Exclude archived chats.

    Returns:
        `Folder`: The created folder.
    """
    return await tg.create_folder(
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


@mcp.tool()
async def update_folder(
    folder_id: int,
    title: str | None = None,
    emoticon: str | None = None,
    add_entities: list[str | int] | None = None,
    remove_entities: list[str | int] | None = None,
) -> Folder:
    """Update an existing chat folder.

    Only the provided fields are modified; everything else is preserved.
    Telegram requires the whole folder to be resent, so the current folder is
    fetched first.

    !IMPORTANT: This modifies a folder on the user's Telegram account. Confirm
    the changes with the user before calling.

    Args:
        folder_id (`int`): The ID of the folder to update. Use `get_folders`
            to find the ID.
        title (`str`, optional): A new title for the folder.
        emoticon (`str`, optional): A new emoji icon. Pass an empty string to
            clear the current icon.
        add_entities (`list[str]`, optional): Entities to add to the folder.
        remove_entities (`list[str]`, optional): Entities to remove from the
            folder.

    Returns:
        `Folder`: The updated folder.
    """
    return await tg.update_folder(
        folder_id,
        title=title,
        emoticon=emoticon,
        add_entities=add_entities,
        remove_entities=remove_entities,
    )


@mcp.tool()
async def delete_folder(folder_id: int) -> str:
    """Delete a chat folder.

    Removes the folder identified by `folder_id`. The chats inside it are not
    deleted; they simply no longer belong to the folder.

    !IMPORTANT: This is destructive and cannot be undone. Confirm the folder ID
    with the user (via `get_folders`) before calling.

    Args:
        folder_id (`int`): The ID of the folder to delete.

    Returns:
        `str`: A success message if deleted, or an error message if failed.
    """
    await tg.delete_folder(folder_id)
    return f"Folder {folder_id} deleted"


@mcp.tool()
async def reorder_folders(folder_ids: list[int]) -> str:
    """Reorder chat folders.

    Sets the order of folders as displayed in the client. The list should
    contain the user folder IDs in the desired order.

    !IMPORTANT: This reorders folders on the user's Telegram account. Confirm
    the intended order with the user (via `get_folders`) before calling.

    Args:
        folder_ids (`list[int]`): The desired order of folder IDs.

    Returns:
        `str`: A success message if reordered, or an error message if failed.
    """
    await tg.reorder_folders(folder_ids)
    return f"Folders reordered: {folder_ids}"


def run_direct_server(
    transport: str = "stdio",
    host: str = "0.0.0.0",
    port: int = 8766,
    auth_token: str | None = None,
) -> None:
    """Run the direct-mode MCP server (no daemon) on the given transport."""
    run_mcp_server(
        mcp, transport=transport, host=host, port=port, auth_token=auth_token
    )
