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
  - [The Setup Wizard (Recommended)](#the-setup-wizard-recommended)
  - [Manual Login](#login)
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
- [`uv`](https://github.com/astral-sh/uv) (Recommended) or `pip`
- [Docker & Docker Compose](https://docs.docker.com/get-docker/) (Optional, for easy database setup)

### Installation

Install the `mcp-telegram` CLI tool:

```bash
uv tool install mcp-telegram --force
```

## ⚙️ Usage

> [!IMPORTANT]
> Please ensure you have read and understood Telegram's [ToS](https://telegram.org/tos) before using this tool. Misuse of this tool may result in account restrictions.

### The Setup Wizard (Recommended)

**New in v0.3.0**: The interactive setup wizard handles everything for you.

```bash
mcp-telegram setup
```

This wizard will:
1. **Configure PostgreSQL** (detects local or starts via Docker Compose)
2. **Run Migrations** to set up the database schema
3. **Set API Credentials** (ID and Hash from [my.telegram.org](https://my.telegram.org/apps))
4. **Authenticate** with your phone number and code
5. **Generate `.env`** for the daemon mode

### Login

If you already have a database configured and just need to authenticate a new account:

```bash
mcp-telegram login
```

### Connect to the MCP server

To use MCP Telegram with clients like Claude Desktop or Cursor, add it to your configuration:

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

### Multi-Terminal Mode (Daemon)

> [!NOTE]
> **Powered by PostgreSQL**: Version 0.3.0 uses a robust PostgreSQL backend for session sharing, fixing conflict issues common with SQLite.

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

#### Running the Daemon

**Option A: Using Docker (Fastest)**
```bash
docker-compose up -d
```

**Option B: Manual Start**
```bash
mcp-telegram daemon
```

#### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `API_ID` | Telegram API ID | Required |
| `API_HASH` | Telegram API Hash | Required |
| `DATABASE_URL` | PostgreSQL connection string | Required for daemon |
| `DAEMON_URL` | Daemon HTTP endpoint | `http://localhost:8765` |

## 🧰 Available Tools

| Tool | Description |
|------|-------------|
| `send_message` | ✉️ Send text or files to users/groups/channels |
| `edit_message` | ✏️ Edit previously sent messages |
| `search_dialogs` | 🔎 Find chats by name or username |
| `get_messages` | 📜 Retrieve chat history |
| `get_draft` | 📋 View current message drafts |
| `media_download` | 📸 Download photos/videos from messages |

## 🛠️ Troubleshooting

### Session Authentication Errors
If you see `'NoneType' object has no attribute 'access_hash'`, it usually means the session cache is inconsistent. 
**Fix**: 
1. `docker-compose down -v` (if using Docker)
2. `mcp-telegram setup` to re-initialize from scratch.

### Database Connection
Ensure PostgreSQL is running and the `DATABASE_URL` in your `.env` is correct. The setup wizard can automatically start a Docker container for you if Docker is installed.

## 🤝 Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

<div align="center">
  <p>Originally made by <a href="https://x.com/dryeab">Yeabsira Driba</a></p>
  <p>v0.3.0 enhancements by <a href="https://github.com/imdeniil">imdeniil</a></p>
</div>
