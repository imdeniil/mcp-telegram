"""MCP Proxy Client - communicates with the Telegram daemon."""

import logging

from typing import Any

import httpx

logger = logging.getLogger(__name__)


class DaemonClient:
    """HTTP client for communicating with the Telegram daemon."""

    def __init__(self, base_url: str = "http://localhost:8765", timeout: float = 30.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        """Initialize the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
            )

    async def disconnect(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        """Make an HTTP request to the daemon.

        Raises:
            RuntimeError: Not connected to daemon
            httpx.HTTPStatusError: HTTP error from daemon
            httpx.RequestError: Connection error
        """
        if self._client is None:
            raise RuntimeError("Not connected to daemon. Call connect() first.")

        try:
            response = await self._client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Daemon error {e.response.status_code}: {e.response.text[:200]}"
            )
            raise
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to daemon at {self._base_url}: {e}")
            raise

    async def health(self) -> dict[str, Any]:
        """Check daemon health."""
        return await self._request("GET", "/health")

    async def get_account(self) -> dict[str, Any]:
        """Get account info."""
        return await self._request("GET", "/account")

    async def send_message(
        self,
        entity: str | int,
        message: str = "",
        file_path: list[str] | None = None,
        reply_to: int | None = None,
    ) -> dict[str, Any]:
        """Send a message."""
        return await self._request(
            "POST",
            "/send_message",
            json={
                "entity": entity,
                "message": message,
                "file_path": file_path,
                "reply_to": reply_to,
            },
        )

    async def edit_message(
        self, entity: str | int, message_id: int, message: str
    ) -> dict[str, Any]:
        """Edit a message."""
        return await self._request(
            "POST",
            "/edit_message",
            json={"entity": entity, "message_id": message_id, "message": message},
        )

    async def delete_message(
        self, entity: str | int, message_ids: list[int]
    ) -> dict[str, Any]:
        """Delete messages."""
        return await self._request(
            "POST",
            "/delete_message",
            json={"entity": entity, "message_ids": message_ids},
        )

    async def get_messages(
        self,
        entity: str | int,
        limit: int = 10,
        start_date: str | None = None,
        end_date: str | None = None,
        offset_id: int = 0,
        reverse: bool = False,
    ) -> dict[str, Any]:
        """Get messages."""
        return await self._request(
            "POST",
            "/get_messages",
            json={
                "entity": entity,
                "limit": limit,
                "start_date": start_date,
                "end_date": end_date,
                "offset_id": offset_id,
                "reverse": reverse,
            },
        )

    async def search_dialogs(
        self, query: str, limit: int = 10, global_search: bool = False
    ) -> dict[str, Any]:
        """Search for dialogs."""
        return await self._request(
            "POST",
            "/search_dialogs",
            json={"query": query, "limit": limit, "global_search": global_search},
        )

    async def get_draft(self, entity: str | int) -> dict[str, Any]:
        """Get draft."""
        return await self._request("POST", "/get_draft", json={"entity": entity, "message": ""})

    async def set_draft(self, entity: str | int, message: str) -> dict[str, Any]:
        """Set draft."""
        return await self._request(
            "POST", "/set_draft", json={"entity": entity, "message": message}
        )

    async def download_media(
        self, entity: str | int, message_id: int, path: str | None = None
    ) -> dict[str, Any]:
        """Download media."""
        return await self._request(
            "POST",
            "/download_media",
            json={"entity": entity, "message_id": message_id, "path": path},
        )

    async def message_from_link(self, link: str) -> dict[str, Any]:
        """Get message from link."""
        return await self._request("POST", f"/message_from_link?link={link}")


# Global client instance
_daemon_client: DaemonClient | None = None


def get_daemon_client() -> DaemonClient:
    """Get or create the global daemon client."""
    global _daemon_client
    if _daemon_client is None:
        import os

        daemon_url = os.environ.get("DAEMON_URL", "http://localhost:8765")
        _daemon_client = DaemonClient(daemon_url)
    return _daemon_client
