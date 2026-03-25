"""MCP Telegram CLI."""

import asyncio
import importlib.metadata
import logging
import os
import sys

from collections.abc import Callable, Coroutine
from functools import wraps
from typing import Any
from uuid import uuid4

import typer

from mcp.types import Tool
from rich.box import ROUNDED
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from mcp_telegram.daemon import DaemonConfig, run_daemon
from mcp_telegram.server import mcp
from mcp_telegram.server_proxy import run_proxy_server
from mcp_telegram.telegram import Telegram

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

app = typer.Typer(
    name="mcp-telegram",
    help="MCP Server for Telegram - with multi-terminal support via daemon mode",
    add_completion=False,
    no_args_is_help=True,
)

console = Console()


def async_command(
    func: Callable[..., Coroutine[Any, Any, None]],
) -> Callable[..., None]:
    """Decorator to handle async Typer commands.

    Args:
        func: An async function that will be wrapped to work with Typer.

    Returns:
        A synchronous function that can be used with Typer.
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        asyncio.run(func(*args, **kwargs))

    return wrapper


@app.command()
def version() -> None:
    """Show the MCP Telegram version."""
    try:
        version = importlib.metadata.version("mcp-telegram")
        console.print(
            Panel.fit(
                f"[bold blue]MCP Telegram version {version}[/bold blue]",
                title="📦 Version",
                border_style="blue",
            )
        )
    except importlib.metadata.PackageNotFoundError:
        console.print(
            Panel.fit(
                "[bold red]MCP Telegram version unknown (package not installed)\
                    [/bold red]",
                title="❌ Error",
                border_style="red",
            )
        )
        sys.exit(1)


@app.command()
@async_command
async def login() -> None:
    """Login to Telegram."""
    console.print(
        Panel.fit(
            "[bold blue]Welcome to MCP Telegram![/bold blue]\n\n"
            "To proceed with login, you'll need your Telegram API credentials:\n"
            "1. Visit [link]https://my.telegram.org/apps[/link]\n"
            "2. Create a new application if you haven't already\n"
            "3. Copy your API ID and API Hash",
            title="🚀 Telegram Authentication",
            border_style="blue",
        )
    )

    tg = Telegram()

    console.print("\n[yellow]Please enter your credentials:[/yellow]")

    try:
        api_id = console.input(
            "\n[bold cyan]🔑 API ID[/bold cyan]\n"
            "[dim]Enter your Telegram API ID (found on my.telegram.org)[/dim]\n"
            "> ",
            password=True,
        )

        api_hash = console.input(
            "\n[bold cyan]🔒 API Hash[/bold cyan]\n"
            "[dim]Enter your Telegram API hash (found on my.telegram.org)[/dim]\n"
            "> ",
            password=True,
        )

        phone = console.input(
            "\n[bold cyan]📱 Phone Number[/bold cyan]\n"
            "[dim]Enter your phone number in international format \
                (e.g., +1234567890)[/dim]\n"
            "> "
        )

        tg.create_client(api_id=api_id, api_hash=api_hash)

        with console.status("[bold green]Connecting to Telegram...", spinner="dots"):
            await tg.client.connect()
            console.print(
                "\n[bold green]✓[/bold green] [dim]Connected to Telegram[/dim]"
            )

        def code_callback() -> str:
            return console.input(
                "\n[bold cyan]🔢 Verification Code[/bold cyan]\n"
                "[dim]Enter the code sent to your Telegram[/dim]\n"
                "> "
            )

        def password_callback() -> str:
            return console.input(
                "\n[bold cyan]🔐 Two-Factor Authentication[/bold cyan]\n"
                "[dim]Enter your 2FA password[/dim]\n"
                "> ",
                password=True,
            )

        await tg.client.start(
            phone=phone,
            code_callback=code_callback,
            password=password_callback,
        )  # type: ignore

        console.print("\n[bold green]✓[/bold green] [dim]Successfully logged in[/dim]")

        user = await tg.client.get_me()

        console.print(
            Panel.fit(
                f"[bold green]Authentication successful![/bold green]\n"
                f"[dim]Welcome, {user.first_name}! You can now use MCP Telegram commands.[/dim]",  # type: ignore  # noqa: E501
                title="🎉 Success",
                border_style="green",
            )
        )

    except ValueError:
        console.print(
            "\n[bold red]✗ Error:[/bold red] API ID must be a number", style="red"
        )
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[bold red]✗ Error:[/bold red] {str(e)}", style="red")
        sys.exit(1)
    finally:
        if tg.client.is_connected():
            # disconnect() is async, but we're in sync context in finally block
            # Use asyncio.run for cleanup
            import asyncio

            asyncio.run(tg.client.disconnect())


@app.command()
def start(
    daemon: bool = typer.Option(
        False, "--daemon", "-d", help="Connect to daemon instead of direct Telegram"
    ),
) -> None:
    """Start the MCP Telegram server.

    By default, connects directly to Telegram (single process mode).
    Use --daemon to connect to a running daemon (multi-terminal mode).
    """
    if daemon:
        console.print("[dim]Starting MCP server in daemon mode...[/dim]")
        run_proxy_server()
    else:
        console.print("[dim]Starting MCP server in direct mode...[/dim]")
        mcp.run()


@app.command()
def daemon(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Host to bind to"),
    port: int = typer.Option(8765, "--port", "-p", help="Port to listen on"),
) -> None:
    """Start the Telegram daemon for multi-terminal support.

    The daemon holds a single Telegram connection and exposes an HTTP API.
    Multiple MCP servers can connect to it simultaneously.
    """
    database_url = os.environ.get("DATABASE_URL")
    api_id = os.environ.get("API_ID")
    api_hash = os.environ.get("API_HASH")
    account_id = os.environ.get("ACCOUNT_ID")

    if not database_url:
        console.print(
            "[bold red]Error:[/bold red] DATABASE_URL environment variable not set.\n"
            "Run 'mcp-telegram setup' first or set it manually."
        )
        raise typer.Exit(1)

    if not api_id or not api_hash:
        console.print(
            "[bold red]Error:[/bold red] API_ID and API_HASH environment variables not set.\n"
            "Run 'mcp-telegram login' first or set them manually."
        )
        raise typer.Exit(1)

    config = DaemonConfig(
        database_url=database_url,
        api_id=int(api_id),
        api_hash=api_hash,
        account_id=uuid4() if account_id else None,
        host=host,
        port=port,
    )

    console.print(
        Panel.fit(
            f"[bold green]Starting Telegram Daemon[/bold green]\n\n"
            f"[dim]Host:[/dim] {host}\n"
            f"[dim]Port:[/dim] {port}\n"
            f"[dim]Database:[/dim] {'***' if database_url else 'Not set'}\n\n"
            f"[yellow]Multiple terminals can now connect via:[/yellow]\n"
            f"  [bold]mcp-telegram start --daemon[/bold]",
            title="🚀 Daemon Mode",
            border_style="green",
        )
    )

    run_daemon(config)


@app.command()
@async_command
async def setup() -> None:
    """Interactive setup wizard for daemon mode.

    Configures PostgreSQL database and Telegram credentials.
    """
    console.print(
        Panel.fit(
            "[bold blue]MCP Telegram Setup Wizard[/bold blue]\n\n"
            "This wizard will help you configure:\n"
            "1. PostgreSQL database connection\n"
            "2. Telegram API credentials\n"
            "3. First-time authentication\n\n"
            "[dim]You can also use Docker for automatic setup.[/dim]",
            title="⚙️ Setup",
            border_style="blue",
        )
    )

    # Step 1: Database configuration
    console.print("\n[bold cyan]Step 1: Database Configuration[/bold cyan]")
    console.print("[dim]Press Enter to use defaults[/dim]\n")

    db_host = Prompt.ask("Database host", default="localhost")
    db_port = Prompt.ask("Database port", default="5432")
    db_name = Prompt.ask("Database name", default="mcp_telegram")
    db_user = Prompt.ask("Database user", default="mcp")
    db_password = Prompt.ask("Database password", password=True)

    database_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

    # Step 2: Telegram credentials
    console.print("\n[bold cyan]Step 2: Telegram API Credentials[/bold cyan]")
    console.print(
        "[dim]Get these from https://my.telegram.org/apps[/dim]\n"
    )

    api_id = Prompt.ask("API ID")
    api_hash = Prompt.ask("API Hash", password=True)

    # Step 3: Test database connection
    console.print("\n[bold cyan]Step 3: Testing Database Connection[/bold cyan]")

    try:
        import asyncpg

        with console.status("[bold green]Connecting to database..."):
            pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1")
            await pool.close()

        console.print("[bold green]✓[/bold green] Database connection successful")
    except Exception as e:
        console.print(f"[bold red]✗[/bold red] Database connection failed: {e}")
        console.print(
            "\n[yellow]Tip:[/yellow] Make sure PostgreSQL is running and the database exists.\n"
            "You can create it with:\n"
            f"  [bold]createdb {db_name}[/bold]\n"
            "Or use Docker:\n"
            "  [bold]docker-compose up -d postgres[/bold]"
        )
        raise typer.Exit(1)

    # Step 4: Create .env file
    console.print("\n[bold cyan]Step 4: Creating Configuration File[/bold cyan]")

    env_content = f"""# MCP Telegram Configuration
# Generated by setup wizard

# Database
DATABASE_URL=postgresql://{db_user}:***@{db_host}:{db_port}/{db_name}

# Telegram API (from https://my.telegram.org/apps)
API_ID={api_id}
API_HASH={api_hash}

# Daemon settings
DAEMON_HOST=0.0.0.0
DAEMON_PORT=8765
"""

    env_path = ".env"
    with open(env_path, "w") as f:
        f.write(env_content)

    console.print(f"[bold green]✓[/bold green] Created {env_path}")

    # Step 5: Instructions
    console.print(
        Panel.fit(
            "[bold green]Setup Complete![/bold green]\n\n"
            "[cyan]Next steps:[/cyan]\n\n"
            "1. [bold]Start the daemon:[/bold]\n"
            "   [dim]source .env && mcp-telegram daemon[/dim]\n\n"
            "2. [bold]In another terminal, run the MCP server:[/bold]\n"
            "   [dim]mcp-telegram start --daemon[/dim]\n\n"
            "3. [bold]Or use Docker Compose:[/bold]\n"
            "   [dim]docker-compose up -d[/dim]\n\n"
            "[yellow]Note:[/yellow] On first run, you'll need to login:\n"
            "   [dim]source .env && mcp-telegram login[/dim]",
            title="🎉 Setup Complete",
            border_style="green",
        )
    )


@app.command()
def logout() -> None:
    """Show instructions on how to logout from Telegram."""
    console.print(
        Panel.fit(
            "[bold blue]How to Logout from Telegram[/bold blue]\n\n"
            "To logout from your Telegram account, please follow these steps:\n\n"
            "1. Open your Telegram app\n"
            "2. Go to [bold]Settings[/bold]\n"
            "3. Select [bold]Privacy and Security[/bold]\n"
            "4. Scroll down to find [bold]'Active Sessions'[/bold]\n"
            "5. Find and terminate the session with the name of your app\n   "
            "(This is the app name you created on [link]my.telegram.org/apps[/link])\n\n"  # noqa: E501
            "[yellow]Note:[/yellow] After logging out, you can use the [bold]clear-session[/bold] "  # noqa: E501
            "command to remove local session data.",
            title="🚪 Logout Instructions",
            border_style="blue",
        )
    )


@app.command()
def clear_session() -> None:
    """Delete the local Telegram session file."""

    session_file = Telegram().session_file.with_suffix(".session")

    if session_file.exists():
        try:
            os.remove(session_file)
            console.print(
                Panel.fit(
                    "[bold green]Session file successfully deleted![/bold green]\n"
                    "[dim]You can now safely create a new session by logging in again.[/dim]",  # noqa: E501
                    title="🗑️ Session Cleared",
                    border_style="green",
                )
            )
        except Exception as e:
            console.print(
                Panel.fit(
                    f"[bold red]Failed to delete session file:[/bold red]\n{str(e)}",
                    title="❌ Error",
                    border_style="red",
                )
            )
    else:
        console.print(
            Panel.fit(
                "[bold yellow]No session file found![/bold yellow]\n"
                "[dim]The session file may have already been deleted or never existed.[/dim]",  # noqa: E501
                title="ℹ️ Info",
                border_style="yellow",
            )
        )


def _format_parameters(schema: dict[str, Any]) -> str:
    """Formats the parameters from a tool's input schema for display."""
    if not schema.get("properties"):
        return "[dim]No parameters[/dim]"

    params: list[str] = []
    properties: dict[str, dict[str, Any]] = schema.get("properties", {})
    required_params: set[str] = set(schema.get("required", []))

    for name, details in properties.items():
        param_type: str = details.get("type", "any")
        description: str = details.get("description", "")
        param_str: str = f"[bold]{name}[/bold]: [italic]{param_type}[/italic]"
        if description:
            param_str += f" - [dim]{description}[/dim]"

        if name in required_params:
            params.append(f"[red]•[/red] {param_str} [bold red](required)[/bold red]")
        else:
            params.append(f"[dim]•[/dim] {param_str}")

    return "\n".join(params) if params else "[dim]No parameters[/dim]"


@app.command()
@async_command
async def tools() -> None:
    """List all available tools in a table format."""
    try:
        tools: list[Tool] = await mcp.list_tools()
    except Exception as e:
        console.print(f"[bold red]Error fetching tools:[/bold red] {e}")
        raise typer.Exit(code=1)

    if not tools:
        console.print("[yellow]No tools available.[/yellow]")
        return

    table = Table(
        title="🔧 Available Tools",
        box=ROUNDED,
        show_header=True,
        header_style="bold blue",
        show_lines=True,
        expand=True,
    )

    table.add_column("Name", style="cyan", width=20, overflow="fold")
    table.add_column("Description", style="dim", ratio=2, overflow="fold")
    table.add_column("Parameters", ratio=3, overflow="fold")

    for tool in tools:
        table.add_row(
            f"[bold]{tool.name}[/bold]",
            tool.description or "[dim]No description[/dim]",
            _format_parameters(tool.inputSchema),
        )

    console.print(table)
