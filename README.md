<div align="center">
  <img src="logo.png" alt="MCP Telegram Logo" width="150"/>
  <h2 style="margin-top: 0">Enable LLMs to control your Telegram</h2>
</div>

<div align="center">
    <a href="https://github.com/dryeab/mcp-telegram/stargazers"><img src="https://img.shields.io/github/stars/dryeab/mcp-telegram?style=social" alt="GitHub stars"></a>
    <a href="https://badge.fury.io/py/mcp-telegram"><img src="https://badge.fury.io/py/mcp-telegram.svg" alt="PyPI version"></a>
    <a href="https://x.com/dryeab"><img src="https://img.shields.io/twitter/follow/dryeab?style=social" alt="Twitter Follow"></a>
</div>
<h3></h3>

**Connect Large Language Models to Telegram via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/introduction).**

Built with [Telethon](https://github.com/LonamiWebs/Telethon), this server allows AI agents to interact with Telegram, enabling features like sending/editing/deleting messages, searching chats, managing drafts, downloading media, and more using the [MTProto](https://core.telegram.org/mtproto).

---
<details>
<summary><strong>Table&nbsp;of&nbsp;Contents</strong></summary>

- [🚀 Getting Started](#-getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
- [⚙️ Usage](#️-usage)
  - [Login](#login)
  - [Connect to the MCP server](#connect-to-the-mcp-server)
  - [Multi-Terminal Mode (Daemon)](#multi-terminal-mode-daemon)
- [🧰 Available Tools](#-available-tools)
  - [📨 Messaging Tools](#-messaging-tools)
  - [🔍 Search & Navigation](#-search--navigation)
  - [📝 Draft Management](#-draft-management)
  - [📂 Media Handling](#-media-handling)
- [🛠️ Troubleshooting](#️-troubleshooting)
- [🤝 Contributing](#-contributing)
- [📝 License](#-license)

</details>

---


## 🚀 Getting Started

### Prerequisites

- Python 3.10 or higher
- [`uv`](https://github.com/astral-sh/uv) Install via the [official uv guide](https://github.com/astral-sh/uv#installation).

### Installation

Install the `mcp-telegram` CLI tool:

```bash
uv tool install mcp-telegram
```

## ⚙️ Usage

> [!IMPORTANT]
> Please ensure you have read and understood Telegram's [ToS](https://telegram.org/tos) before using this tool. Misuse of this tool may result in account restrictions.

The `mcp-telegram` command-line tool is your entry point.

```bash
mcp-telegram --help # See all commands
```

### Login

First, authenticate with your Telegram account:

```bash
mcp-telegram login
```

This interactive command will prompt you for:

- **API ID & API Hash:** Obtain these from [my.telegram.org/apps](https://my.telegram.org/apps).
- **Phone Number:** Your Telegram-registered phone number (international format, e.g., `+1234567890`).
- **Verification Code:** Sent to your Telegram account upon first login.
- **2FA Password:** If you have Two-Factor Authentication enabled.

Your credentials are securely stored in the session file for future use.

> [!WARNING]
> Keep your API credentials private and never share them publicly

> [!NOTE]
> Use `mcp-telegram logout` to logout from current session or `mcp-telegram clear-session` to remove all stored session data.

### Connect to the MCP server

To use MCP Telegram with MCP clients like Claude Desktop or Cursor, you'll need to configure the MCP server. The configuration process varies by client and operating system.

For detailed setup instructions, please refer to:

- [Claude Desktop MCP Setup Guide](https://modelcontextprotocol.io/quickstart/user)
- [Cursor MCP Documentation](https://docs.cursor.com/context/model-context-protocol)

The configuration file should contain:

```json
{
  "mcpServers": {
    "mcp-telegram": {
      "command": "mcp-telegram" /* Use full path if client can't find the command (e.g. "/usr/local/bin/mcp-telegram"). See IMPORTANT section below for full path instructions. */,
      "args": ["start"],
      "env": {
        "API_ID": "<your_api_id>",
        "API_HASH": "<your_api_hash>"
      }
    }
  }
}
```

> [!Note]
> Configuration paths vary by OS and client. For example:
>
> - macOS: `~/Library/Application Support/Claude/` or `~/.cursor/`
> - Windows: `%APPDATA%\Claude\` or `%APPDATA%\Cursor\`

> [!IMPORTANT]
> If your client cannot execute `mcp-telegram` despite it being accessible in the terminal, try using the full path to the executable. You can find this by running `which mcp-telegram` (macOS/Linux) or `where mcp-telegram` (Windows) in your terminal. Replace the `command` value in the configuration with the full path.

After saving the configuration file, restart your application.

### Multi-Terminal Mode (Daemon)

> [!NOTE]
> **New in v0.2.0**: Use Telegram from multiple terminals without re-authentication!

By default, each MCP server instance connects directly to Telegram using SQLite sessions, which can cause conflicts. The new **daemon mode** solves this by running a single Telegram connection that multiple terminals can share.

#### Architecture

```
┌─────────────────┐
│  MCP Terminal 1 │──┐
├─────────────────┤  │     ┌─────────────────┐     ┌──────────────┐
│  MCP Terminal 2 │──┼────▶│ Telegram Daemon │────▶│  PostgreSQL  │
├─────────────────┤  │     │  (one process)  │     │  (sessions)  │
│  MCP Terminal N │──┘     └────────┬────────┘     └──────────────┘
└─────────────────┘                 │
                                    ▼
                              ┌──────────┐
                              │ Telegram │
                              └──────────┘
```

The daemon uses PostgreSQL for session storage, enabling:
- **Shared sessions** across multiple terminals
- **Persistent authentication** - login once, use everywhere
- **No conflicts** - single Telegram connection managed centrally

#### Quick Start with Docker (Recommended)

1. **Create `.env` file**:
```bash
cp .env.example .env
# Edit .env with your API_ID and API_HASH
```

2. **Start the stack**:
```bash
docker-compose up -d
```

This starts:
- PostgreSQL database (auto-migrates schema)
- Telegram daemon (connects to Telegram)

3. **Login (first time only)**:
```bash
docker-compose exec daemon mcp-telegram login
```

4. **Configure your MCP client**:
```json
{
  "mcpServers": {
    "mcp-telegram": {
      "command": "mcp-telegram",
      "args": ["start", "--daemon"],
      "env": {
        "DAEMON_URL": "http://localhost:8765"
      }
    }
  }
}
```

#### Manual Setup (Without Docker)

1. **Setup PostgreSQL**:
```bash
# Create database
createdb mcp_telegram

# Run migrations
psql -d mcp_telegram -f migrations/001_initial_schema.sql

# Or use the setup wizard (interactive)
mcp-telegram setup
```

2. **Create `.env` file**:
```bash
DATABASE_URL=postgresql://user:password@localhost:5432/mcp_telegram
API_ID=your_api_id
API_HASH=your_api_hash
```

3. **Start the daemon**:
```bash
source .env && mcp-telegram daemon
```

4. **Start MCP servers** (in other terminals):
```bash
mcp-telegram start --daemon
```

#### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `API_ID` | Telegram API ID | Required |
| `API_HASH` | Telegram API Hash | Required |
| `DATABASE_URL` | PostgreSQL connection string | Required for daemon |
| `DAEMON_URL` | Daemon HTTP endpoint | `http://localhost:8765` |
| `DAEMON_HOST` | Daemon bind host | `0.0.0.0` |
| `DAEMON_PORT` | Daemon HTTP port | `8765` |

#### Commands

| Command | Description |
|---------|-------------|
| `mcp-telegram daemon` | Start the Telegram daemon |
| `mcp-telegram start --daemon` | Start MCP server in daemon mode |
| `mcp-telegram start` | Start MCP server in direct mode (legacy) |
| `mcp-telegram setup` | Interactive setup wizard |
| `mcp-telegram tools` | List all available MCP tools |

#### Migration from Single-Terminal

If you were using the direct mode before:

1. Export your session or simply login again in daemon mode
2. Update your MCP client config to use `["start", "--daemon"]`
3. Start the daemon before using MCP clients

The old `mcp-telegram start` (without `--daemon`) still works for single-terminal use.

## 🧰 Available Tools

Here's a comprehensive list of tools you can use to interact with Telegram through MCP:

### 📨 Messaging Tools

| Tool             | Description                                                   |
| ---------------- | ------------------------------------------------------------- |
| `send_message`   | ✉️ Send text messages or files to any user, group, or channel |
| `edit_message`   | ✏️ Modify content of previously sent messages                 |
| `delete_message` | 🗑️ Remove one or multiple messages                            |
| `get_messages`   | 📜 Retrieve message history with advanced filtering options   |

### 🔍 Search & Navigation

| Tool                | Description                                             |
| ------------------- | ------------------------------------------------------- |
| `search_dialogs`    | 🔎 Find users, groups, and channels by name or username |
| `message_from_link` | 🔗 Access specific messages using Telegram links        |

### 📝 Draft Management

| Tool        | Description                                |
| ----------- | ------------------------------------------ |
| `get_draft` | 📋 View current message draft for any chat |
| `set_draft` | ✍️ Create or clear message drafts          |

### 📂 Media Handling

| Tool             | Description                                             |
| ---------------- | ------------------------------------------------------- |
| `media_download` | 📸 Download photos, videos, and documents from messages |

> [!Note]
> For detailed parameter information and example use cases, run `mcp-telegram tools` in your terminal.

## 🛠️ Troubleshooting

### Database Locked Errors (Direct Mode)

Running multiple `mcp-telegram` instances using the _same session file_ can cause `database is locked` errors due to Telethon's SQLite session storage. Ensure only one instance uses a session file at a time.

**Solution**: Use daemon mode (`mcp-telegram start --daemon`) which supports multiple terminals.

<details>
<summary>Force-Stopping Existing Processes</summary>

If you need to stop potentially stuck processes:

- **macOS / Linux:** `pkill -f "mcp-telegram"`
- **Windows:** `taskkill /F /IM mcp-telegram.exe /T` (Check Task Manager for the exact process name)

</details>

### Daemon Connection Errors

If MCP servers can't connect to the daemon:

1. **Check daemon is running**: `curl http://localhost:8765/health`
2. **Check environment**: Ensure `DAEMON_URL` is set correctly
3. **Check Docker**: `docker-compose ps` to verify services are healthy

### Docker Issues

```bash
# View daemon logs
docker-compose logs -f daemon

# Restart services
docker-compose restart

# Reset everything (WARNING: deletes data)
docker-compose down -v
docker-compose up -d
```

## 🤝 Contributing

We welcome contributions! If you'd like to help improve MCP Telegram, please feel free to submit issues, feature requests, or pull requests. Your feedback and contributions help make this project better for everyone.

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

<div align="center">
  <p>Made with ❤️ by <a href="https://x.com/dryeab">Yeabsira Driba</a></p>
</div>
