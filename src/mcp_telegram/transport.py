"""Transport helpers for running the MCP server.

Runs a FastMCP server over `stdio`, `sse`, or `streamable-http`. For the HTTP
transports an optional Bearer-token auth can be enabled so the server can be
safely exposed behind a reverse proxy on a public host.
"""

import logging
import os

from typing import Any, cast

import httpx
import uvicorn

from mcp.server.fastmcp import FastMCP

# Host validation exists in mcp >= 1.7; older versions don't have it.
try:
    from mcp.server.transport_security import TransportSecuritySettings  # type: ignore
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


class TokenAuthMiddleware:
    """ASGI auth requiring a shared token, browser-friendly.

    Accepts any of:
      - ``Authorization: Bearer <token>`` header (programmatic clients).
      - a cookie (so the web dashboard works after a one-time ?token= open).
      - ``?token=<token>`` query param, which additionally sets the cookie on
        the response (bookmark ``https://host/?token=…`` once).
    Non-HTTP scopes (lifespan) are passed through.
    """

    def __init__(self, app: Any, token: str, cookie_name: str = "mcp_token") -> None:
        self.app = app
        self.token = token
        self.cookie = cookie_name
        self._cookie_match = f"{cookie_name}={token}".encode()
        self._bearer = f"Bearer {token}".encode()
        self._query = f"token={token}".encode()
        self._set_cookie = (
            f"{cookie_name}={token}; Path=/; HttpOnly; SameSite=Lax; "
            f"Max-Age=2592000"
        ).encode()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") in ("http", "websocket"):
            headers: list[tuple[bytes, bytes]] = list(scope.get("headers") or [])  # type: ignore[arg-type]
            bearer_ok = cookie_ok = False
            for name, value in headers:
                if name == b"authorization" and value == self._bearer:
                    bearer_ok = True
                elif name == b"cookie" and self._cookie_match in value:
                    cookie_ok = True
            query_ok = self._query in (scope.get("query_string") or b"")
            if not (bearer_ok or cookie_ok or query_ok):
                if scope["type"] == "http":
                    await _send_unauthorized(send)
                else:
                    await send({"type": "websocket.close", "code": 1008})
                return
            # Set the cookie when the caller proves knowledge via ?token=.
            if query_ok and scope["type"] == "http":
                send = self._wrap_send_with_cookie(send)  # type: ignore[assignment]
        await self.app(scope, receive, send)

    def _wrap_send_with_cookie(self, send: Any) -> Any:
        target = self

        async def wrapped(message: Any) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"set-cookie", target._set_cookie))
                message = {**message, "headers": headers}
            await send(message)

        return wrapped


class ReverseProxy:
    """ASGI reverse-proxy that streams HTTP requests to a target base URL."""

    _HOP = {
        b"connection",
        b"keep-alive",
        b"proxy-authenticate",
        b"proxy-authorization",
        b"te",
        b"trailer",
        b"trailers",
        b"transfer-encoding",
        b"upgrade",
    }

    def __init__(self, target_url: str) -> None:
        self.target = target_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(timeout=300.0))

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            return  # only HTTP is proxied
        method = scope["method"]
        path = scope.get("path", "") or "/"
        qs = (scope.get("query_string") or b"").decode("latin-1")
        url = f"{self.target}{path}" + (f"?{qs}" if qs else "")
        raw_headers: list[tuple[bytes, bytes]] = list(scope.get("headers") or [])  # type: ignore[arg-type]
        headers = [
            (k, v)
            for k, v in raw_headers
            if k.lower() not in self._HOP and k.lower() != b"host"
        ]
        body = b""
        while True:
            msg = await receive()
            if msg.get("type") == "http.request":
                body += msg.get("body") or b""
                if not msg.get("more_body"):
                    break
        resp = None
        try:
            req = self.client.build_request(method, url, headers=headers, content=body)  # type: ignore[arg-type]
            resp = await self.client.send(req, stream=True)
            out_headers = [
                (k, v)
                for k, v in resp.headers.raw
                if k.lower() not in self._HOP
                and k.lower() not in (b"content-length", b"content-encoding")
            ]
            await send(
                {
                    "type": "http.response.start",
                    "status": resp.status_code,
                    "headers": out_headers,
                }
            )
            async for chunk in resp.aiter_raw():
                await send(
                    {"type": "http.response.body", "body": chunk, "more_body": True}
                )
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except Exception as e:
            logger.warning(f"reverse-proxy error to {url}: {e}")
            await _send_bad_gateway(send)
        finally:
            if resp is not None:
                await resp.aclose()


class RouterApp:
    """Route /sse and /messages/* to the MCP app; everything else to a proxy."""

    def __init__(self, sse_app: Any, proxy: Any) -> None:
        self.sse = sse_app
        self.proxy = proxy

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        path = scope.get("path", "") or ""
        if path == "/sse" or path.startswith("/messages"):
            await self.sse(scope, receive, send)
        else:
            await self.proxy(scope, receive, send)


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


async def _send_bad_gateway(send: Any) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 502,
            "headers": [[b"content-type", b"application/json"]],
        }
    )
    await send(
        {"type": "http.response.body", "body": b'{"detail":"bad gateway"}'}
    )


def run_mcp_server(
    mcp: FastMCP,
    transport: str = "stdio",
    host: str = "0.0.0.0",
    port: int = 8766,
    auth_token: str | None = None,
    daemon_url: str | None = None,
) -> None:
    """Run a FastMCP server on the given transport.

    Args:
        mcp (`FastMCP`): The MCP server instance to run.
        transport (`str`): ``stdio`` (default), ``sse``, or ``streamable-http``.
        host (`str`): Bind address for HTTP transports. Ignored for stdio.
        port (`int`): Bind port for HTTP transports. Ignored for stdio.
        auth_token (`str | None`, optional): If set, HTTP requests require the
            token (``Authorization: Bearer <token>`` header, a cookie, or
            ``?token=``). Strongly recommended for public exposure.
        daemon_url (`str | None`, optional): If set, non-MCP paths (``/``,
            ``/api/*``, ``/health`` …) are reverse-proxied to this daemon URL,
            turning the server into a single public gateway that also serves the
            web dashboard. Ignored for stdio.

    Raises:
        `ValueError`: If ``transport`` is not one of the supported values.
    """
    transport = (transport or "stdio").lower()

    if transport == "stdio":
        mcp.run()
        return

    if transport == "sse":
        _configure_host_validation(mcp)
        mcp_app: Any = cast(Any, mcp.sse_app())  # type: ignore
    elif transport == "streamable-http":
        _configure_host_validation(mcp)
        mcp_app = cast(Any, mcp.streamable_http_app())  # type: ignore
    else:
        raise ValueError(
            f"Unsupported transport {transport!r}; "
            "use one of: stdio, sse, streamable-http."
        )

    if daemon_url:
        app: Any = RouterApp(mcp_app, ReverseProxy(daemon_url))
        logger.info("Dashboard reverse-proxy -> %s enabled", daemon_url)
    else:
        app = mcp_app

    if auth_token:
        app = TokenAuthMiddleware(app, auth_token)
        logger.info("MCP HTTP auth enabled (Bearer header / cookie)")
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
    uvicorn.run(app, host=host, port=port)  # type: ignore
