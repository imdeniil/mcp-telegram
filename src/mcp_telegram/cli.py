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
from rich.prompt import Confirm, Prompt
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
    """Login to Telegram.

    Supports two modes:
    - Direct mode (default): Uses SQLite session file
    - Daemon mode: Uses PostgreSQL when DATABASE_URL is set

    For daemon mode, ensure DATABASE_URL, API_ID, and API_HASH are set
    in environment or .env file (created by 'mcp-telegram setup').
    """
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

    # Check for daemon mode (PostgreSQL)
    database_url = os.environ.get("DATABASE_URL")
    use_postgres = bool(database_url)

    if use_postgres:
        console.print("\n[dim]Daemon mode: Using PostgreSQL session storage[/dim]")

    tg = Telegram()
    client = None
    pool = None
    session = None
    account_id = None

    console.print("\n[yellow]Please enter your credentials:[/yellow]")

    try:
        # Get credentials from env or prompt
        api_id = os.environ.get("API_ID")
        api_hash = os.environ.get("API_HASH")

        if not api_id:
            api_id = console.input(
                "\n[bold cyan]🔑 API ID[/bold cyan]\n"
                "[dim]Enter your Telegram API ID (found on my.telegram.org)[/dim]\n"
                "> "
            )
        api_id = int(api_id)

        if not api_hash:
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

        # For PostgreSQL mode, we need to setup the session differently
        if use_postgres:
            from uuid import uuid4

            from telethon import TelegramClient

            from mcp_telegram.session import create_session_pool

            # Create database pool
            pool = await create_session_pool(database_url)

            # Get or create account
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id FROM telegram_accounts "
                    "WHERE is_active = TRUE ORDER BY created_at LIMIT 1"
                )

                if row:
                    account_id = row["id"]
                    console.print("[dim]Using existing account[/dim]")
                else:
                    # Create new account
                    account_id = uuid4()
                    console.print(f"[dim]DEBUG: Creating account with name='Main', api_id={api_id}[/dim]")
                    await conn.execute(
                        "INSERT INTO telegram_accounts "
                        "(id, name, api_id, api_hash, phone, is_active) "
                        "VALUES ($1, $2, $3, $4, $5, TRUE)",
                        account_id,
                        "Main",
                        int(api_id),
                        api_hash,
                        phone,
                    )
                    console.print("[dim]Created new account[/dim]")

            # Initialize PostgreSQL session
            from mcp_telegram.session import init_session
            session = await init_session(pool, account_id)

            # Create client with PostgreSQL session
            client = TelegramClient(
                session=session,
                api_id=int(api_id),
                api_hash=api_hash,
            )
        else:
            # Standard SQLite mode
            tg.create_client(api_id=api_id, api_hash=api_hash)
            client = tg.client

        with console.status("[bold green]Connecting to Telegram...", spinner="dots"):
            await client.connect()
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

        await client.start(
            phone=phone,
            code_callback=code_callback,
            password=password_callback,
        )  # type: ignore

        # Save session for PostgreSQL mode
        if use_postgres:
            session.save()
            # Update account with user info
            me = await client.get_me()
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE telegram_accounts
                    SET user_id = $1, username = $2,
                        last_connected_at = NOW(), updated_at = NOW()
                    WHERE id = $3
                    """,
                    me.id,
                    getattr(me, "username", None),
                    account_id,
                )
            # Wait for fire-and-forget save to complete
            await session.wait_for_pending_saves()

        console.print("\n[bold green]✓[/bold green] [dim]Successfully logged in[/dim]")

        user = await client.get_me()

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
        # Cleanup
        if client and client.is_connected():
            await client.disconnect()
        if session and hasattr(session, "wait_for_pending_saves"):
            await session.wait_for_pending_saves()
            await asyncio.sleep(0.5)
        if pool:
            await pool.close()


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
            "[bold red]Error:[/bold red] API_ID and API_HASH "
            "environment variables not set.\n"
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
def setup() -> None:
    """Interactive setup wizard for daemon mode.

    All-in-one setup: database, credentials, login, and optionally start daemon.
    """
    try:
        # Run async setup and get config for daemon if needed
        result = asyncio.run(_async_setup())
    except Exception as e:
        console.print(f"[bold red]✗[/bold red] Error during setup: {e}")
        return

    # Start daemon outside of async context if requested
    if result and result.get("start_daemon"):
        console.print("\n[bold green]Starting daemon...[/bold green]")
        console.print("[dim]Press Ctrl+C to stop[/dim]\n")

        # Set environment variables for daemon
        os.environ["DATABASE_URL"] = result["database_url"]
        os.environ["API_ID"] = result["api_id"]
        os.environ["API_HASH"] = result["api_hash"]

        # Import and run daemon
        from mcp_telegram.daemon import DaemonConfig, run_daemon

        config = DaemonConfig(
            database_url=result["database_url"],
            api_id=int(result["api_id"]),
            api_hash=result["api_hash"],
            host="0.0.0.0",
            port=8765,
        )

        console.print(
            Panel.fit(
                "[bold green]Daemon Running![/bold green]\n\n"
                "[dim]Host:[/dim] 0.0.0.0\n"
                "[dim]Port:[/dim] 8765\n\n"
                "[yellow]In another terminal, run:[/yellow]\n"
                "  [bold]mcp-telegram start --daemon[/bold]\n\n"
                "[dim]Or configure MCP client:[/dim]\n"
                '  [bold]"args": ["start", "--daemon"][/bold]',
                title="🚀 Daemon Mode",
                border_style="green",
            )
        )

        run_daemon(config)


import sys
import traceback

def version_callback(value: bool):
    if value:
        print("mcp-telegram version 0.2.1")
        raise typer.Exit()

@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", callback=version_callback, is_eager=True, help="Show version"
    ),
):
    """MCP Telegram CLI."""
    pass

async def _async_setup() -> dict | None:
    """Async part of setup wizard."""
    pool = None
    client = None
    session = None
    account_id = None

    console.print(
        Panel.fit(
            "[bold blue]MCP Telegram Setup Wizard[/bold blue]\n\n"
            "This wizard will:\n"
            "1. Configure PostgreSQL database (create + migrate)\n"
            "2. Set Telegram API credentials\n"
            "3. Authenticate with Telegram\n"
            "4. Optionally start the daemon\n\n"
            "[dim]Press Ctrl+C at any time to cancel[/dim]",
            title="⚙️ Setup",
            border_style="blue",
        )
    )

    # ========================================
    # Step 1: Database configuration
    # ========================================
    console.print("\n[bold cyan]━━━ Step 1/4: Database Configuration ━━━[/bold cyan]")
    console.print("[dim]Press Enter to use defaults[/dim]\n")

    db_host = Prompt.ask("Database host", default="localhost")
    db_port = Prompt.ask("Database port", default="5432")
    db_name = Prompt.ask("Database name", default="mcp_telegram")
    db_user = Prompt.ask("Database user", default="mcp")
    db_password = Prompt.ask("Database password", password=True)

    database_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

    # ========================================
    # Step 2: Telegram credentials
    # ========================================
    console.print("\n[bold cyan]━━━ Step 2/4: Telegram API Credentials ━━━[/bold cyan]")
    console.print("[dim]Get these from https://my.telegram.org/apps[/dim]\n")

    api_id = Prompt.ask("API ID")
    api_hash = Prompt.ask("API Hash", password=True)

    # ========================================
    # Step 3: Create database and run migrations
    # ========================================
    console.print("\n[bold cyan]━━━ Step 3/4: Database Setup ━━━[/bold cyan]")

    pool = None
    import subprocess

    from pathlib import Path

    import asyncpg

    database_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    postgres_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/postgres"

    async def check_postgres_connection(url: str) -> bool:
        """Check if PostgreSQL is accessible."""
        try:
            conn = await asyncio.wait_for(
                asyncpg.connect(url),
                timeout=3.0
            )
            await conn.close()
            return True
        except Exception:
            return False

    async def wait_for_postgres(url: str, max_attempts: int = 30) -> bool:
        """Wait for PostgreSQL to become available."""
        for attempt in range(max_attempts):
            if await check_postgres_connection(url):
                return True
            await asyncio.sleep(1)
            if attempt % 5 == 4:
                console.print(
                    f"[dim]Still waiting for PostgreSQL... "
                    f"({attempt + 1}/{max_attempts})[/dim]"
                )
        return False

    # Check if PostgreSQL is already running with user's config
    if await check_postgres_connection(postgres_url):
        console.print("[bold green]✓[/bold green] PostgreSQL is already running")
    else:
        console.print(
            "[yellow]PostgreSQL is not running with "
            "specified configuration.[/yellow]"
        )

        # Check if docker-compose.yml exists
        compose_file = Path("docker-compose.yml")
        if not compose_file.exists():
            console.print("[bold red]✗[/bold red] No docker-compose.yml found.")
            raise typer.Exit(1)

        use_docker = Confirm.ask(
            "Start PostgreSQL via Docker with your configuration?",
            default=True
        )

        if not use_docker:
            console.print(
                "[yellow]Please start PostgreSQL manually and "
                "run setup again.[/yellow]"
            )
            raise typer.Exit(1)

        console.print(
            "[dim]Starting PostgreSQL container with your configuration...[/dim]"
        )

        try:
            # Set environment variables for docker-compose
            env = os.environ.copy()
            env["DB_USER"] = db_user
            env["DB_PASSWORD"] = db_password
            env["DB_NAME"] = db_name
            env["DB_PORT"] = str(db_port)

            # Stop existing container if any
            subprocess.run(
                ["docker-compose", "down"],
                capture_output=True,
                env=env,
            )

            # Start postgres container with user's configuration
            result = subprocess.run(
                ["docker-compose", "up", "-d", "postgres"],
                capture_output=True,
                text=True,
                env=env,
            )

            if result.returncode != 0:
                console.print(
                    f"[bold red]✗[/bold red] Failed to start container: "
                    f"{result.stderr}"
                )
                raise typer.Exit(1)

            console.print("[bold green]✓[/bold green] PostgreSQL container started")

            # Wait for PostgreSQL to be ready
            console.print("[dim]Waiting for PostgreSQL to be ready...[/dim]")
            if not await wait_for_postgres(postgres_url):
                console.print(
                    "[bold red]✗[/bold red] PostgreSQL did not "
                    "become ready in time"
                )
                raise typer.Exit(1)

            console.print("[bold green]✓[/bold green] PostgreSQL is ready")

        except FileNotFoundError:
            console.print(
                "[bold red]✗[/bold red] docker-compose not found. "
                "Please install Docker Compose."
            )
            raise typer.Exit(1)

    # Create database and run migrations
    try:
        with console.status("[bold green]Connecting to PostgreSQL..."):
            conn = await asyncpg.connect(postgres_url)

        # Check if database exists
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", db_name
        )

        if not exists:
            console.print(f"[dim]Creating database '{db_name}'...[/dim]")
            await conn.execute(f'CREATE DATABASE "{db_name}"')
            console.print(f"[bold green]✓[/bold green] Database '{db_name}' created")
        else:
            console.print(f"[bold green]✓[/bold green] Database '{db_name}' exists")

        await conn.close()

        # Now connect to our database and run migrations
        with console.status("[bold green]Running migrations..."):
            pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)

            # Check if schema exists
            async with pool.acquire() as conn:
                version = await conn.fetchval(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = 'schema_version'"
                )

                if not version:
                    # Run migrations
                    migrations_dir = (
                        Path(__file__).parent.parent.parent / "migrations"
                    )
                    if migrations_dir.exists():
                        for migration_file in sorted(
                            migrations_dir.glob("*.sql")
                        ):
                            console.print(
                                f"[dim]Running {migration_file.name}...[/dim]"
                            )
                            sql = migration_file.read_text()
                            await conn.execute(sql)
                        console.print("[bold green]✓[/bold green] Migrations completed")
                    else:
                        console.print(
                            "[yellow]⚠[/yellow] Migrations directory not found. "
                            "Run manually: psql -d {db_name} "
                            "-f migrations/001_initial_schema.sql"
                        )
                else:
                    console.print("[bold green]✓[/bold green] Schema already exists")

        console.print("[bold green]✓[/bold green] Database setup complete")
    except Exception as e:
        console.print(f"[bold red]✗[/bold red] Database setup failed: {e}")
        raise typer.Exit(1)

    # ========================================
    # Step 4: Telegram Authentication
    # ========================================
    console.print(
        "\n[bold cyan]━━━ Step 4/4: Telegram Authentication ━━━[/bold cyan]"
    )

    phone = Prompt.ask(
        "Phone number",
        default="",
    )

    if not phone:
        console.print(
            "[yellow]Skipping authentication. "
            "Run 'mcp-telegram login' later.[/yellow]"
        )
    else:
        try:
            from uuid import uuid4

            from telethon import TelegramClient

            # Get or create account
            async with pool.acquire() as conn:  # type: ignore
                row = await conn.fetchrow(
                    "SELECT id FROM telegram_accounts "
                    "WHERE is_active = TRUE ORDER BY created_at LIMIT 1"
                )

                if row:
                    account_id = row["id"]
                    console.print("[dim]Using existing account[/dim]")
                else:
                    # Create new account
                    account_id = uuid4()
                    console.print(f"[dim]DEBUG: Creating account with name='Main', api_id={api_id}[/dim]")
                    await conn.execute(
                        "INSERT INTO telegram_accounts "
                        "(id, name, api_id, api_hash, phone, is_active) "
                        "VALUES ($1, $2, $3, $4, $5, TRUE)",
                        account_id,
                        "Main",
                        int(api_id),
                        api_hash,
                        phone,
                    )
                    console.print("[dim]Created new account[/dim]")

            # Initialize PostgreSQL session
            from mcp_telegram.session import init_session
            session = await init_session(pool, account_id)

            # Create client with PostgreSQL session
            client = TelegramClient(
                session=session,
                api_id=int(api_id),
                api_hash=api_hash,
            )

            with console.status(
                "[bold green]Connecting to Telegram...", spinner="dots"
            ):
                await client.connect()
                console.print("[bold green]✓[/bold green] Connected to Telegram")

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

            await client.start(
                phone=phone,
                code_callback=code_callback,
                password=password_callback,
            )  # type: ignore

            # Save session
            session.save()
            await asyncio.sleep(0.5)  # Wait for fire-and-forget save

            # Update account with user info
            me = await client.get_me()
            async with pool.acquire() as conn:  # type: ignore
                await conn.execute(
                    """
                    UPDATE telegram_accounts
                    SET user_id = $1, username = $2,
                        last_connected_at = NOW(), updated_at = NOW()
                    WHERE id = $3
                    """,
                    me.id,
                    getattr(me, "username", None),
                    account_id,
                )

            console.print(f"[bold green]✓[/bold green] Logged in as {me.first_name}")

            await client.disconnect()
            if session and hasattr(session, "wait_for_pending_saves"):
                await session.wait_for_pending_saves()
            # Give a bit more time for the database driver to finish
            await asyncio.sleep(0.5)

        except Exception:
            console.print("\n[bold red]✗[/bold red] Authentication failed")
            # Use the traceback module to be 100% sure we see the error
            import traceback
            console.print(traceback.format_exc())
            console.print("[dim]You can retry with 'mcp-telegram login' later[/dim]")
            if session and hasattr(session, "wait_for_pending_saves"):
                await session.wait_for_pending_saves()
            await asyncio.sleep(0.5)

    # Close pool
    if pool:
        await pool.close()

    # ========================================
    # Create .env file
    # ========================================
    env_content = f"""# MCP Telegram Configuration
# Generated by setup wizard

# Database
DATABASE_URL={database_url}

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

    console.print(f"\n[bold green]✓[/bold green] Created {env_path}")

    # ========================================
    # Ask to start daemon
    # ========================================
    start_daemon = Confirm.ask(
        "\nStart the daemon now?",
        default=True,
    )

    if start_daemon:
        # Return config for daemon startup (handled outside async context)
        return {
            "start_daemon": True,
            "database_url": database_url,
            "api_id": api_id,
            "api_hash": api_hash,
        }
    else:
        # Final instructions
        console.print(
            Panel.fit(
                "[bold green]Setup Complete![/bold green]\n\n"
                "[cyan]To start the daemon:[/cyan]\n"
                "  [bold]source .env && mcp-telegram daemon[/bold]\n\n"
                "[cyan]In other terminals, use:[/cyan]\n"
                "  [bold]mcp-telegram start --daemon[/bold]\n\n"
                "[dim]Or configure your MCP client with:[/dim]\n"
                '  [bold]"args": ["start", "--daemon"][/bold]',
                title="🎉 Ready!",
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
