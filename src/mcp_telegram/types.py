"""Types for MCP Telegram Server"""

import typing

from datetime import datetime
from enum import Enum

from pydantic import BaseModel
from telethon import hints, types, utils  # type: ignore
from telethon.tl import custom, patched  # type: ignore


DATE_INPUT_GUIDE = """\
How to enter dates for date-filtered tools (get_messages, export_messages).

Date parameters accept ISO 8601 strings. A timezone offset is recommended;
values without one are interpreted as UTC.

Formats:
  - Full:      2026-06-09T18:03:54+00:00
  - Date only: 2026-06-09            (-> 2026-06-09T00:00:00+00:00)
  - Date+time: 2026-06-09T18:03:54   (naive -> treated as UTC)

Common windows (use the current date when computing these):
  - Last week:      start_date = <now minus 7 days>
  - Last 30 days:   start_date = <now minus 30 days>
  - A specific day: start_date = 2026-06-09T00:00:00+00:00
                    end_date   = 2026-06-09T23:59:59+00:00
  - From a date to now: set only start_date (end_date defaults to now).

Tools:
  - get_messages(entity, start_date, end_date, limit, offset_id):
      one chat, one page. Pass the returned next_offset_id as offset_id on the
      next call to fetch the following page; stop when has_more is false.
  - export_messages(entities, start_date, end_date?, per_chat_limit, max_chats):
      collect messages across chats. start_date is required (bounds the work).
      Leave entities unset to export the most recent chats (up to max_chats).

Tips:
  - Always provide start_date for exports (required) and to bound large fetches.
  - For big ranges, page with limit + offset_id instead of one giant limit.
  - Relative phrasing like "last week" means compute the absolute ISO date from
    the current time, then pass it.
"""


class DialogType(Enum):
    """The type of a dialog."""

    USER = "user"
    GROUP = "group"
    CHANNEL = "channel"
    BOT = "bot"


class Dialog(BaseModel):
    id: int
    """The ID of the dialog."""
    title: str
    """The title of the dialog."""
    username: str | None = None
    """The username of the dialog."""
    phone_number: str | None = None
    """The phone number of the dialog."""
    type: DialogType
    """The type of the dialog."""
    unread_messages_count: int
    """The number of unread messages in the dialog."""
    can_send_message: bool
    """Whether the user can send messages to the dialog."""

    @staticmethod
    def get_dialog_type(entity: hints.Entity) -> "DialogType":
        """Get the type of a dialog from a telethon entity."""
        if isinstance(entity, types.User):
            if entity.bot:
                return DialogType.BOT
            else:
                return DialogType.USER
        elif isinstance(entity, types.Chat):
            return DialogType.GROUP
        else:
            if entity.megagroup:
                return DialogType.GROUP
            else:
                return DialogType.CHANNEL

    @staticmethod
    def from_entity(entity: hints.Entity, can_send_message: bool = False) -> "Dialog":
        """Convert a `telethon.hints.Entity` object to a `Dialog` object.

        Args:
            entity (`telethon.hints.Entity`): The entity to convert.

        Returns:
            `Dialog`: The converted Dialog object.
        """

        id: int = utils.get_peer_id(entity)  # type: ignore
        title = utils.get_display_name(entity)  # type: ignore
        type: DialogType = Dialog.get_dialog_type(entity)
        username = entity.username if not isinstance(entity, types.Chat) else None
        phone_number = entity.phone if isinstance(entity, types.User) else None

        return Dialog(
            id=id,  # type: ignore
            title=title,
            type=type,
            username=username,
            phone_number=phone_number,
            unread_messages_count=0,
            can_send_message=can_send_message,
        )


class Folder(BaseModel):
    """A Telegram chat folder (dialog filter)."""

    id: int
    """The ID of the folder. User folders have IDs >= 2; 0 = all chats, 1 = archive."""
    title: str
    """The title of the folder."""
    emoticon: str | None = None
    """The emoji icon of the folder, if any."""
    contacts: bool = False
    """Whether the folder includes contacts."""
    non_contacts: bool = False
    """Whether the folder includes non-contacts."""
    groups: bool = False
    """Whether the folder includes groups."""
    broadcasts: bool = False
    """Whether the folder includes channels (broadcasts)."""
    bots: bool = False
    """Whether the folder includes bots."""
    exclude_muted: bool = False
    """Whether muted chats are excluded from the folder."""
    exclude_read: bool = False
    """Whether read chats are excluded from the folder."""
    exclude_archived: bool = False
    """Whether archived chats are excluded from the folder."""
    include_peer_ids: list[int] = []
    """Peer IDs explicitly included in the folder."""
    exclude_peer_ids: list[int] = []
    """Peer IDs explicitly excluded from the folder."""
    pinned_peer_ids: list[int] = []
    """Peer IDs pinned within the folder."""
    is_chatlist: bool = False
    """Whether this is a shareable folder (chatlist)."""
    is_default: bool = False
    """Whether this is the default 'All Chats' folder (not editable)."""

    @staticmethod
    def from_filter(filter: typing.Any) -> "Folder":
        """Convert a `telethon` dialog filter object to a `Folder` object.

        Args:
            filter (`typing.Any`): A telethon dialog filter, i.e. an instance of
                `DialogFilter`, `DialogFilterChatlist`, or `DialogFilterDefault`.

        Returns:
            `Folder`: The converted Folder object.
        """
        if isinstance(filter, types.DialogFilterDefault):
            return Folder(id=0, title="All Chats", is_default=True)

        is_chatlist = isinstance(filter, types.DialogFilterChatlist)

        title_text = ""
        if filter.title is not None:
            raw_title = filter.title.text
            title_text = raw_title if isinstance(raw_title, str) else str(raw_title)

        def _peer_ids(peers: typing.Any) -> list[int]:
            return [int(utils.get_peer_id(p)) for p in (peers or [])]  # type: ignore

        include_peer_ids = _peer_ids(filter.include_peers)
        pinned_peer_ids = _peer_ids(filter.pinned_peers)
        exclude_peer_ids = (
            _peer_ids(filter.exclude_peers) if not is_chatlist else []
        )

        emoticon = filter.emoticon if isinstance(filter.emoticon, str) else None

        return Folder(
            id=int(filter.id),
            title=title_text,
            emoticon=emoticon,
            contacts=bool(getattr(filter, "contacts", None) or False),
            non_contacts=bool(getattr(filter, "non_contacts", None) or False),
            groups=bool(getattr(filter, "groups", None) or False),
            broadcasts=bool(getattr(filter, "broadcasts", None) or False),
            bots=bool(getattr(filter, "bots", None) or False),
            exclude_muted=bool(getattr(filter, "exclude_muted", None) or False),
            exclude_read=bool(getattr(filter, "exclude_read", None) or False),
            exclude_archived=bool(getattr(filter, "exclude_archived", None) or False),
            include_peer_ids=include_peer_ids,
            exclude_peer_ids=exclude_peer_ids,
            pinned_peer_ids=pinned_peer_ids,
            is_chatlist=is_chatlist,
            is_default=False,
        )


class Media(BaseModel):
    """A media object."""

    media_id: int
    """The ID of the media."""
    mime_type: str | None = None
    """The MIME type of the media."""
    file_name: str | None = None
    """The name of the file."""
    file_size: int | None = None
    """The size of the file."""

    @staticmethod
    def from_message(message: custom.Message) -> typing.Union["Media", None]:
        """Convert a `telethon.tl.custom.Message` object to a `Media` object.

        Args:
            message (`telethon.tl.custom.Message`): The message to convert.

        Returns:
            `Media`: The converted Media object.
        """

        if message.media and message.file:
            media_id: int
            if message.photo:
                media_id = message.photo.id
            elif message.document:
                media_id = message.document.id
            else:
                # Fallback to message ID if no specific media ID is available
                media_id = message.id

            file_name = (
                message.file.name if isinstance(message.file.name, str) else None
            )

            return Media(
                media_id=media_id,
                mime_type=message.file.mime_type,
                file_name=file_name,
                file_size=message.file.size,
            )

        return None


class DownloadedMedia(BaseModel):
    """A downloaded media object."""

    path: str
    """The path to the downloaded media."""
    media: Media
    """The media object."""


class Message(BaseModel):
    """A single message from an entity."""

    message_id: int
    """The ID of the message."""
    sender_id: int | None = None
    """The ID of the user who sent the message."""
    message: str | None = None
    """The message text."""
    outgoing: bool
    """Whether the message is outgoing."""
    date: datetime | None = None
    """The date and time the message was sent."""
    media: Media | None = None
    """The media associated with the message."""
    reply_to: int | None = None
    """The message ID that this message is replying to."""

    @staticmethod
    def from_message(message: patched.Message) -> "Message":
        """Convert a `telethon.tl.patched.Message` object to a `Message` object.

        Args:
            message (`telethon.tl.patched.Message`): The message to convert.

        Returns:
            `Message`: The converted Message object.
        """

        sender_id: int | None = None
        if message.from_id:
            sender_id = int(utils.get_peer_id(message.from_id))  # type: ignore
        media = Media.from_message(message)
        message_text: str | None = (
            message.text if isinstance(message.text, str) else None  # type: ignore
        )
        reply_to: int | None = None
        if message.reply_to and isinstance(message.reply_to, types.MessageReplyHeader):
            try:
                reply_to = (
                    int(message.reply_to.reply_to_msg_id)
                    if message.reply_to.reply_to_msg_id
                    else None
                )
            except (AttributeError, TypeError, ValueError):
                reply_to = None

        return Message(
            message_id=message.id,
            sender_id=sender_id,
            message=message_text,
            outgoing=message.out,
            date=message.date,
            media=media,
            reply_to=reply_to,
        )


class Messages(BaseModel):
    """A list of messages from an entity and the dialog the messages belong to."""

    messages: list[Message]
    """The list of messages."""
    dialog: Dialog | None = None
    """The dialog the messages belong to."""
    next_offset_id: int | None = None
    """Cursor for pagination: the oldest message ID in this page. Pass it as
    `offset_id` on the next `get_messages` call to fetch the following page.
    `None` when there are no older messages in the requested range."""
    has_more: bool = False
    """Whether more messages may remain in the requested range (page stopped at
    `limit`, not at the date boundary)."""


class ChatMessages(BaseModel):
    """Messages from a single chat, part of a cross-chat export."""

    dialog: Dialog
    """The dialog the messages belong to."""
    messages: list[Message]
    """The messages collected from this chat for the export window."""


class ExportResult(BaseModel):
    """Result of a cross-chat message export over a date window."""

    results: list[ChatMessages]
    """Per-chat message batches, one entry per processed chat."""
    chats_processed: int
    """Number of chats included in this export."""
    truncated: bool
    """True if at least one chat hit `per_chat_limit` (more messages may exist
    in the window for that chat; page it via `get_messages(offset_id=...)`)."""
