"""Transport helpers for running the MCP server.

Runs a FastMCP server over `stdio`, `sse`, or `streamable-http`. For the HTTP
transports an optional Bearer-token auth can be enabled so the server can be
safely exposed behind a reverse proxy on a public host.
"""

import logging
import os

from typing import Any

import uvicorn

from mcp.server.fastmcp import FastMCP

# Host validation exists in mcp >= 1.7; older versions don't have it.
try:
    from mcp.server.transport_security import (
        TransportSecuritySettings,  # type: ignore[import-not-found]
    )
except ImportError:
    TransportSecuritySettings = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


def _configure_host_validation(mcp: FastMCP) -> None:
    """Allow non-localhost Host headers (public domain / container hostname).

    FastMCP enables DNS-rebinding protection by default and only allows
    localhost, returning 421 "Invalid Host header" for anything else. For an
    HTTP transport reached via a public domain or a container hostname, set
    ``MCP_ALLOWED_HOSTS`` (comma-separated, e.g. ``mcp.example.com:*``) or
    ``MCP_DISABLE_HOST_VALIDATION=true`` to drop the check entirely (then rely
    on Bearer auth + your reverse proxy).
    """
    if TransportSecuritySettings is None:
        return  # older mcp without host validation -> nothing to configure

    disable = os.environ.get("MCP_DISABLE_HOST_VALIDATION", "").lower() in (
        "1",
        "true",
        "yes",
    )
    extra = [
        h.strip()
        for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",")
        if h.strip()
    ]

    if disable:
        mcp.settings.transport_security = TransportSecuritySettings(  # type: ignore[assignment]
            enable_dns_rebinding_protection=False
        )
    elif extra:
        hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"] + extra
        mcp.settings.transport_security = TransportSecuritySettings(  # type: ignore[assignment]
            allowed_hosts=hosts
        )


class BearerAuthMiddleware:
    """Minimal ASGI middleware enforcing ``Authorization: Bearer <token>``.

    Wraps any ASGI app (FastMCP's Starlette app). Non-HTTP scopes (e.g. the
    lifespan startup) are passed through. HTTP/websocket requests without a
    matching bearer token get a 401 / websocket close.
    """

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self._expected = f"Bearer {token}"

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") in ("http", "websocket"):
            headers: list[tuple[bytes, bytes]] = list(scope.get("headers") or [])  # type: ignore[arg-type]
            auth = ""
            for name, value in headers:
                if name == b"authorization":
                    auth = value.decode("latin-1")
                    break
            if auth != self._expected:
                if scope["type"] == "http":
                    await _send_unauthorized(send)
                else:
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)


async def _send_unauthorized(send: Any) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                [b"content-type", b"application/json"],
                [b"www-authenticate", b"Bearer"],
            ],
        }
    )
    await send(
        {"type": "http.response.body", "body": b'{"detail":"unauthorized"}'}
    )


def run_mcp_server(
    mcp: FastMCP,
    transport: str = "stdio",
    host: str = "0.0.0.0",
    port: int = 8766,
    auth_token: str | None = None,
) -> None:
    """Run a FastMCP server on the given transport.

    Args:
        mcp (`FastMCP`): The MCP server instance to run.
        transport (`str`): ``stdio`` (default), ``sse``, or ``streamable-http``.
        host (`str`): Bind address for HTTP transports. Ignored for stdio.
        port (`int`): Bind port for HTTP transports. Ignored for stdio.
        auth_token (`str | None`, optional): If set, HTTP transports require an
            ``Authorization: Bearer <auth_token>`` header. Ignored for stdio.

    Raises:
        `ValueError`: If ``transport`` is not one of the supported values.
    """
    transport = (transport or "stdio").lower()

    if transport == "stdio":
        mcp.run()
        return

    if transport == "sse":
        _configure_host_validation(mcp)
        app: Any = mcp.sse_app()  # type: ignore[assignment]
    elif transport == "streamable-http":
        _configure_host_validation(mcp)
        app = mcp.streamable_http_app()  # type: ignore[assignment]
    else:
        raise ValueError(
            f"Unsupported transport {transport!r}; "
            "use one of: stdio, sse, streamable-http."
        )

    if auth_token:
        app = BearerAuthMiddleware(app, auth_token)
        logger.info("MCP HTTP auth enabled (Bearer token required)")
    else:
        logger.warning(
            "Starting MCP HTTP server WITHOUT auth. Set MCP_AUTH_TOKEN before "
            "exposing the server publicly."
        )

    logger.info(
        "MCP server starting: transport=%s host=%s port=%s",
        transport,
        host,
        port,
    )
    uvicorn.run(app, host=host, port=port)  # type: ignore[arg-type]
