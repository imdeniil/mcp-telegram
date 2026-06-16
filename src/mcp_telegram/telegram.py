"""Telegram client wrapper."""

import itertools
import logging

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import SecretStr
from pydantic_settings import BaseSettings
from telethon import TelegramClient, hints, types, utils  # type: ignore
from telethon.tl import custom, functions, patched  # type: ignore
from xdg_base_dirs import xdg_state_home

from mcp_telegram.types import (
    ChatMessages,
    Dialog,
    DownloadedMedia,
    ExportResult,
    Folder,
    Media,
    Message,
    Messages,
)
from mcp_telegram.utils import get_unique_filename, parse_telegram_url

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Settings for the Telegram client."""

    api_id: str
    api_hash: SecretStr


class Telegram:
    """Wrapper around `telethon.TelegramClient` class."""

    def __init__(self):
        self._state_dir = xdg_state_home() / "mcp-telegram"
        self._state_dir.mkdir(parents=True, exist_ok=True)

        self._session_file = self._state_dir / "session"

        self._downloads_dir = self._state_dir / "downloads"
        self._downloads_dir.mkdir(parents=True, exist_ok=True)

        self._client: TelegramClient | None = None

    @property
    def client(self) -> TelegramClient:
        if self._client is None:
            raise RuntimeError("Client not created!")
        return self._client

    @property
    def session_file(self) -> Path:
        return self._session_file

    def create_client(
        self, api_id: str | None = None, api_hash: str | None = None
    ) -> TelegramClient:
        """Create a Telegram client.

        If `api_id` and `api_hash` are not provided, the client
        will use the default values from the `Settings` class.

        Args:
            api_id (`int`, optional): The API ID for the Telegram client.
            api_hash (`str`, optional): The API hash for the Telegram client.

        Returns:
            `telethon.TelegramClient`: The created Telegram client.

        Raises:
            `pydantic_core.ValidationError`: If `api_id` and `api_hash`
            are not provided.
        """
        if self._client is not None:
            return self._client

        settings: Settings
        if api_id is None or api_hash is None:
            settings = Settings()  # type: ignore
        else:
            settings = Settings(api_id=api_id, api_hash=SecretStr(api_hash))

        self._client = TelegramClient(
            session=self._session_file,
            api_id=int(settings.api_id),
            api_hash=settings.api_hash.get_secret_value(),
        )

        return self._client

    async def send_message(
        self,
        entity: str | int,
        message: str = "",
        file_path: list[str] | None = None,
        reply_to: int | None = None,
    ) -> None:
        """Send a message to a Telegram user, group, or channel.

        Args:
            entity (`str | int`): The recipient of the message.
            message (`str`, optional): The message to send.
            file_path (`list[str]`, optional): The list of paths to the files
                to be sent.
            reply_to (`int`, optional): The message ID to reply to.

        Raises:
            `FileNotFoundError`: If a file does not exist or is not a file.
        """

        if file_path:
            for path in file_path:
                _path = Path(path)
                if not _path.exists() or not _path.is_file():
                    logger.error(f"File {path} does not exist or is not a file.")
                    raise FileNotFoundError(
                        f"File {path} does not exist or is not a file."
                    )

        await self.client.send_message(
            entity,
            message,
            file=file_path,  # type: ignore
            reply_to=reply_to,  # type: ignore
        )

    async def edit_message(
        self, entity: str | int, message_id: int, message: str
    ) -> None:
        """Edit a message from a specific entity.

        Args:
            entity (`str | int`): The identifier of the entity.
            message_id (`int`): The ID of the message to edit.
            message (`str`): The message to edit the message to.
        """
        await self.client.edit_message(entity, message_id, message)

    async def delete_message(self, entity: str | int, message_ids: list[int]) -> None:
        """Delete a message from a specific entity.

        Args:
            entity (`str | int`): The identifier of the entity.
            message_ids (`list[int]`): The IDs of the messages to delete.
        """
        await self.client.delete_messages(entity, message_ids)

    async def get_draft(self, entity: str | int) -> str:
        """Get the draft message from a specific entity.

        Args:
            entity (`str | int`): The identifier of the entity.

        Returns:
            `str`: The draft message from the specific entity.
        """
        draft = await self.client.get_drafts(entity)

        assert isinstance(draft, custom.Draft)

        if isinstance(draft.text, str):  # type: ignore
            return draft.text

        return ""

    async def set_draft(self, entity: str | int, message: str) -> None:
        """Set a draft message for a specific entity.

        Args:
            entity (`str | int`): The identifier of the entity.
            message (`str`): The message to save as a draft.
        """

        peer_id = await self.client.get_peer_id(entity)
        draft = await self.client.get_drafts(peer_id)

        assert isinstance(draft, custom.Draft)

        await draft.set_message(message)  # type: ignore

    async def get_messages(
        self,
        entity: str | int,
        limit: int = 20,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        unread: bool = False,
        mark_as_read: bool = False,
        offset_id: int | None = None,
    ) -> Messages:
        """Get messages from a specific entity.

        Args:
            entity (`str | int`):
                The entity to get messages from.
            limit (`int`, optional):
                The maximum number of messages to get. Defaults to 20.
            start_date (`datetime`, optional):
                The start date of the messages to get.
            end_date (`datetime`, optional):
                The end date of the messages to get.
            unread (`bool`, optional):
                Whether to get only unread messages. Defaults to False.
            mark_as_read (`bool`, optional):
                Whether to mark the messages as read. Defaults to False.
            offset_id (`int`, optional):
                Pagination cursor: fetch messages older than this message ID.
                Use the `next_offset_id` returned by a previous call to page
                through a large date range. Defaults to None (start from the
                newest / `end_date`).

        Returns:
            `Messages`:
                A page of messages ordered newest to oldest, plus `next_offset_id`
                and `has_more` for pagination.
        """

        if end_date is None:
            end_date = datetime.now(timezone.utc)

        # make it very old if start_date is not provided
        if start_date is None:
            start_date = end_date - timedelta(days=10000)

        # make sure the dates are timezone-aware
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)

        _entity = await self.client.get_entity(entity)
        assert isinstance(_entity, hints.Entity)
        dialog = Dialog.from_entity(_entity)

        if unread:
            if not dialog or dialog.unread_messages_count == 0:
                return Messages(messages=[], dialog=dialog)
            limit = min(limit, dialog.unread_messages_count)

        results: list[Message] = []
        hit_boundary = False
        iter_kwargs: dict[str, Any] = {"offset_date": end_date}
        if offset_id:
            iter_kwargs["offset_id"] = offset_id
        async for message in self.client.iter_messages(_entity, **iter_kwargs):  # type: ignore
            # Skip service messages and empty messages immediately
            if not isinstance(message, patched.Message) or isinstance(
                message, patched.MessageService | patched.MessageEmpty
            ):
                continue

            if message.date is None:
                continue

            if message.date < start_date:
                hit_boundary = True
                break

            if len(results) >= limit:
                break  # page full; more may remain in the window

            if mark_as_read:
                try:
                    await message.mark_read()
                except Exception as e:
                    logger.warning(f"Failed to mark message {message.id} as read: {e}")

            results.append(Message.from_message(message))

        has_more = (not hit_boundary) and len(results) >= limit
        next_offset_id = results[-1].message_id if (results and has_more) else None

        return Messages(
            messages=results,
            dialog=dialog,
            next_offset_id=next_offset_id,
            has_more=has_more,
        )

    async def export_messages(
        self,
        entities: list[str | int] | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        per_chat_limit: int = 100,
        max_chats: int = 30,
    ) -> ExportResult:
        """Collect messages across multiple chats for a date window.

        When `entities` is None, the account's most recent dialogs (up to
        `max_chats`) are used. The result is bounded by `per_chat_limit` per
        chat; page individual chats further via `get_messages(offset_id=...)`.

        Args:
            entities (`list[str | int] | None`, optional): Chats to export. If
                None, recent dialogs (up to `max_chats`) are exported.
            start_date (`datetime`): Start of the export window. Required.
            end_date (`datetime`, optional): End of the window. Defaults to now.
            per_chat_limit (`int`, optional): Max messages per chat. Defaults to
                100, clamped to [1, 500].
            max_chats (`int`, optional): Max chats when `entities` is None.
                Defaults to 30, clamped to [1, 100].

        Returns:
            `ExportResult`: Per-chat message batches with truncation flag.

        Raises:
            `ValueError`: If `start_date` is not provided.
        """
        if start_date is None:
            raise ValueError("start_date is required for export")

        if end_date is None:
            end_date = datetime.now(timezone.utc)
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)

        per_chat_limit = max(1, min(per_chat_limit, 500))
        max_chats = max(1, min(max_chats, 100))

        # Resolve the list of entities to export.
        resolved: list[hints.Entity] = []
        if entities is None:
            async for dialog in self.client.iter_dialogs(limit=max_chats):  # type: ignore
                entity = dialog.entity  # type: ignore
                if entity is not None and isinstance(entity, hints.Entity):
                    resolved.append(entity)
        else:
            for entity in entities:
                try:
                    resolved.append(await self.client.get_entity(entity))  # type: ignore
                except Exception as e:
                    logger.warning(f"export: could not resolve entity {entity}: {e}")

        results: list[ChatMessages] = []
        truncated = False
        for entity in resolved:
            try:
                peer_id = utils.get_peer_id(entity)  # type: ignore
                page = await self.get_messages(
                    peer_id,  # type: ignore
                    limit=per_chat_limit,
                    start_date=start_date,
                    end_date=end_date,
                )
                dialog = page.dialog if page.dialog is not None else Dialog.from_entity(entity)
                if len(page.messages) >= per_chat_limit:
                    truncated = True
                results.append(ChatMessages(dialog=dialog, messages=page.messages))
            except Exception as e:
                logger.warning(f"export: failed for entity {entity}: {e}")

        return ExportResult(
            results=results,
            chats_processed=len(results),
            truncated=truncated,
        )

    async def download_media(
        self, entity: str | int, message_id: int, path: str | None = None
    ) -> DownloadedMedia:
        """Download media attached to a specific message to a unique local file.

        Args:
            entity (`str | int`): The chat/user where the message exists.
            message_id (`int`): The ID of the message containing the media.

        Returns:
            `DownloadedMedia`: An object containing the absolute path
                             and media details of the downloaded file.
        """

        # Fetch the specific message
        message = await self.client.get_messages(entity, ids=message_id)  # type: ignore

        if not message or not isinstance(message, patched.Message):
            raise ValueError(
                f"Message {message_id} not found or invalid in entity {entity}."
            )

        media = Media.from_message(message)
        if not media:
            raise ValueError(
                f"Message {message_id} in entity {entity} does not contain \
                    downloadable media."
            )

        filename = get_unique_filename(message)
        if path:
            filepath = Path(path) / filename
        else:
            filepath = self._downloads_dir / filename

        # Attempt to download the media to the specified file path
        try:
            downloaded_path = await message.download_media(file=filepath)  # type: ignore
        except Exception as e:
            logger.error(
                f"Error during media download for message {message_id} "
                f"in entity {entity}: {e}",
                exc_info=True,
            )
            raise e

        if downloaded_path and isinstance(downloaded_path, str):
            absolute_path = str(Path(downloaded_path).resolve())
            logger.info(
                f"Successfully downloaded media for message {message_id} \
                    to {absolute_path}."
            )
            return DownloadedMedia(path=absolute_path, media=media)

        raise ValueError(
            f"Failed to download media for message {message_id}. "
            f"download_media returned: {downloaded_path}"
        )

    async def message_from_link(self, link: str) -> Message:
        """Get a message from a link.

        Args:
            link (`str`): The link to get the message from.

        Returns:
            `Message`: The message from the link.

        Raises:
            `ValueError`: If the link is not a valid Telegram link.
        """

        # Parse the link to get the entity and message ID
        parsed_result = parse_telegram_url(link)

        if parsed_result is None:
            raise ValueError(
                f"Could not parse valid entity/message ID from link: {link}"
            )

        entity, message_id = parsed_result

        # Fetch the specific message using the parsed entity and ID
        message = await self.client.get_messages(entity, ids=message_id)  # type: ignore

        if not message or not isinstance(message, patched.Message):
            raise ValueError(
                f"Could not retrieve message {message_id} from entity {entity} \
                    (parsed from link: {link})"
            )

        return Message.from_message(message)

    async def _can_send_message(self, entity: hints.Entity) -> bool:
        """Check if the logged-in account can send messages to an entity.

        Args:
            entity (`hints.Entity`): The entity to check.

        Returns:
            `bool`: Whether the account can send messages to the entity.
        """

        if isinstance(entity, types.User):
            return True
        else:
            try:
                permissions = await self.client.get_permissions(entity, "me")
                assert isinstance(permissions, custom.ParticipantPermissions)

                if permissions.is_creator or (
                    permissions.is_admin and permissions.post_messages
                ):
                    return True

                if isinstance(entity, types.Channel) and entity.broadcast:
                    return False  # Regular members can't send to broadcast channels

                if permissions.is_banned:
                    if not isinstance(
                        permissions.participant,
                        types.ChannelParticipantBanned,
                    ):
                        logger.warning(
                            f"Unexpected participant type: "
                            f"{type(permissions.participant)}"
                        )
                        return False
                    return not permissions.participant.banned_rights.send_messages

                banned_rights = await self.client.get_permissions(entity)
                if not isinstance(banned_rights, types.ChatBannedRights):
                    logger.warning(
                        f"Unexpected banned_rights type: {type(banned_rights)}"
                    )
                    return False

                return not banned_rights.send_messages

            except Exception as e:
                logger.warning(f"Failed to get permissions for entity {entity}: {e}")
                return False

    async def search_dialogs(
        self, query: str, limit: int, global_search: bool = False
    ) -> list[Dialog]:
        """Search for users, groups, and channels globally.

        Args:
            query (`str`): The search query.
            limit (`int`): Maximum number of results to return.
            global_search (`bool`, optional): Whether to search globally.
                Defaults to False.

        Returns:
            `list[Dialog]`: A list of Dialog objects representing the search results.

        Raises:
            `ValueError`: If the query is empty or the limit is not greater than 0.
        """
        if not query:
            raise ValueError("Query cannot be empty!")

        if limit <= 0:
            raise ValueError("Limit must be greater than 0!")

        response: Any = await self.client(
            functions.contacts.SearchRequest(
                q=query,
                limit=limit,
            )
        )

        assert isinstance(response, types.contacts.Found)

        priority: dict[int, int] = {}
        for i, peer in enumerate(
            itertools.chain(response.my_results, response.results)
            if global_search
            else response.my_results
        ):
            peer_id = await self.client.get_peer_id(peer)
            priority[peer_id] = i

        result: list[Dialog] = []
        for x in itertools.chain(response.users, response.chats):
            if isinstance(x, hints.Entity):
                peer_id = await self.client.get_peer_id(x)
                if peer_id in priority:
                    can_send_message = await self._can_send_message(x)
                    try:
                        dialog = Dialog.from_entity(x, can_send_message)
                        result.append(dialog)
                    except Exception as e:
                        logger.warning(f"Failed to get dialog for entity {x.id}: {e}")

        # Sort results based on priority
        result.sort(key=lambda x: priority.get(x.id))  # type: ignore

        return result

    async def get_folders(self) -> list[Folder]:
        """Get all chat folders (dialog filters) of the logged-in account.

        Returns:
            `list[Folder]`: The list of folders, including the default
                'All Chats' folder if the server reports one.
        """
        response: Any = await self.client(  # type: ignore
            functions.messages.GetDialogFiltersRequest()
        )
        assert isinstance(response, types.messages.DialogFilters)
        return [Folder.from_filter(f) for f in response.filters]

    async def get_folder_dialogs(
        self, folder_id: int, limit: int = 100
    ) -> list[Dialog]:
        """Get the dialogs (chats) belonging to a specific folder.

        Args:
            folder_id (`int`): The folder ID (use `get_folders` to find it).
                `0` lists all non-archived chats, `1` lists archived chats.
            limit (`int`, optional): Maximum number of dialogs to return.
                Defaults to 100.

        Returns:
            `list[Dialog]`: The dialogs contained in the folder.

        Raises:
            `ValueError`: If `limit` is not greater than 0.
        """
        if limit <= 0:
            raise ValueError("Limit must be greater than 0!")

        result: list[Dialog] = []
        async for dialog in self.client.iter_dialogs(  # type: ignore
            folder=folder_id, limit=limit
        ):
            entity = dialog.entity  # type: ignore
            if entity is None:
                continue
            try:
                built = Dialog.from_entity(entity)  # type: ignore
                built.unread_messages_count = dialog.unread_count or 0  # type: ignore
                result.append(built)
            except Exception as e:
                logger.warning(
                    f"Failed to build dialog for folder {folder_id}: {e}"
                )

        return result

    async def create_folder(
        self,
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
            title (`str`): The title of the folder.
            emoticon (`str`, optional): An emoji icon for the folder.
            include_entities (`list[str | int]`, optional): Entities (chat IDs,
                usernames, phone numbers) to explicitly include.
            exclude_entities (`list[str | int]`, optional): Entities to explicitly
                exclude.
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

        Raises:
            `ValueError`: If the title is empty, or if any provided entity
                cannot be resolved (no changes are made in that case).
        """
        if not title:
            raise ValueError("Folder title cannot be empty!")

        folder_id = await self._next_folder_id()
        include_peers = await self._resolve_input_peers(include_entities)
        exclude_peers = await self._resolve_input_peers(exclude_entities)

        dialog_filter = types.DialogFilter(
            id=folder_id,
            title=types.TextWithEntities(text=title, entities=[]),
            emoticon=emoticon,
            pinned_peers=[],
            include_peers=include_peers,
            exclude_peers=exclude_peers,
            contacts=contacts or None,
            non_contacts=non_contacts or None,
            groups=groups or None,
            broadcasts=broadcasts or None,
            bots=bots or None,
            exclude_muted=exclude_muted or None,
            exclude_read=exclude_read or None,
            exclude_archived=exclude_archived or None,
        )
        try:
            await self.client(
                functions.messages.UpdateDialogFilterRequest(
                    id=folder_id, filter=dialog_filter
                )
            )
        except Exception as e:
            logger.error(
                f"Failed to create folder {title!r} (id={folder_id}): {e}",
                exc_info=True,
            )
            raise e

        return Folder.from_filter(dialog_filter)

    async def update_folder(
        self,
        folder_id: int,
        title: str | None = None,
        emoticon: str | None = None,
        add_entities: list[str | int] | None = None,
        remove_entities: list[str | int] | None = None,
    ) -> Folder:
        """Update an existing chat folder.

        Telegram requires the whole folder to be resent, so the current folder
        is fetched first and only the requested fields are modified.

        Args:
            folder_id (`int`): The ID of the folder to update.
            title (`str`, optional): A new title for the folder.
            emoticon (`str`, optional): A new emoji icon. Pass an empty string to
                clear the current icon.
            add_entities (`list[str | int]`, optional): Entities to add to the
                folder.
            remove_entities (`list[str | int]`, optional): Entities to remove
                from the folder.

        Returns:
            `Folder`: The updated folder.

        Raises:
            `ValueError`: If the folder does not exist or is not editable.
        """
        target = await self._get_raw_filter(folder_id)
        if target is None:
            raise ValueError(
                f"Folder {folder_id} not found or is not editable "
                "(the default 'All Chats' and shareable folders cannot be updated)."
            )

        new_title = target.title
        if title is not None:
            new_title = types.TextWithEntities(text=title, entities=[])

        new_emoticon = target.emoticon
        if emoticon is not None:
            new_emoticon = emoticon or None

        include_peers: list[Any] = list(target.include_peers or [])
        if add_entities:
            include_peers.extend(await self._resolve_input_peers(add_entities))
        if remove_entities:
            remove_ids: set[int] = {
                int(utils.get_peer_id(p))  # type: ignore
                for p in await self._resolve_input_peers(remove_entities)
            }
            include_peers = [
                p
                for p in include_peers
                if int(utils.get_peer_id(p)) not in remove_ids  # type: ignore
            ]

        dialog_filter = types.DialogFilter(
            id=target.id,
            title=new_title,
            emoticon=new_emoticon,
            pinned_peers=list(target.pinned_peers or []),
            include_peers=include_peers,
            exclude_peers=list(target.exclude_peers or []),
            contacts=target.contacts,
            non_contacts=target.non_contacts,
            groups=target.groups,
            broadcasts=target.broadcasts,
            bots=target.bots,
            exclude_muted=target.exclude_muted,
            exclude_read=target.exclude_read,
            exclude_archived=target.exclude_archived,
        )
        await self.client(
            functions.messages.UpdateDialogFilterRequest(
                id=folder_id, filter=dialog_filter
            )
        )
        return Folder.from_filter(dialog_filter)

    async def delete_folder(self, folder_id: int) -> None:
        """Delete a chat folder by its ID.

        Chats inside the folder are not deleted; they simply no longer belong
        to the folder.

        Args:
            folder_id (`int`): The ID of the folder to delete. Must be a user
                folder (ID >= 2); system folders ('All Chats', archive) cannot
                be deleted.

        Raises:
            `ValueError`: If `folder_id` refers to a system folder (< 2).
        """
        if folder_id < 2:
            raise ValueError(
                f"Cannot delete folder {folder_id}: only user folders "
                "(IDs >= 2) can be deleted."
            )

        await self.client(
            functions.messages.UpdateDialogFilterRequest(
                id=folder_id, filter=None
            )
        )

    async def reorder_folders(self, folder_ids: list[int]) -> None:
        """Reorder chat folders.

        Args:
            folder_ids (`list[int]`): The desired order of folder IDs. Should
                contain all user folder IDs (the server ignores unknown IDs).

        Raises:
            `ValueError`: If `folder_ids` is empty.
        """
        if not folder_ids:
            raise ValueError("folder_ids cannot be empty!")

        await self.client(
            functions.messages.UpdateDialogFiltersOrderRequest(
                order=list(folder_ids)
            )
        )

    async def _resolve_input_peers(
        self, entities: list[str | int] | None
    ) -> list[Any]:
        """Resolve a list of entity identifiers into `InputPeer` objects.

        Unresolvable entities are skipped with a warning.

        Args:
            entities (`list[str | int] | None`): The entities to resolve.

        Returns:
            `list[Any]`: The resolved `InputPeer` objects.
        """
        if not entities:
            return []

        peers: list[Any] = []
        failed: list[str] = []
        for entity in entities:
            try:
                peers.append(await self.client.get_input_entity(entity))  # type: ignore
            except Exception as e:
                logger.warning(f"Failed to resolve entity {entity}: {e}")
                failed.append(str(entity))

        if failed:
            raise ValueError(
                f"Could not resolve {len(failed)} entity/entities: {failed}. "
                "No changes were made; fix or remove them and retry."
            )

        return peers

    async def _next_folder_id(self) -> int:
        """Find the next free folder ID (>= 2) for a new user folder."""
        response: Any = await self.client(  # type: ignore
            functions.messages.GetDialogFiltersRequest()
        )
        assert isinstance(response, types.messages.DialogFilters)

        max_id = 1
        for f in response.filters:
            if isinstance(f, types.DialogFilter | types.DialogFilterChatlist):
                if f.id > max_id:
                    max_id = f.id

        return max(max_id + 1, 2)

    async def _get_raw_filter(self, folder_id: int) -> Any | None:
        """Get a raw `DialogFilter` by ID, skipping non-editable filters.

        Args:
            folder_id (`int`): The folder ID to look up.

        Returns:
            `Any | None`: The matching `DialogFilter`, or `None` if not found
                or not editable.
        """
        response: Any = await self.client(  # type: ignore
            functions.messages.GetDialogFiltersRequest()
        )
        assert isinstance(response, types.messages.DialogFilters)

        for f in response.filters:
            if isinstance(f, types.DialogFilter) and f.id == folder_id:
                return f

        return None
