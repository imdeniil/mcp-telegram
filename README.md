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

Built with [Telethon](https://github.com/LonamiWebs/Telethon), this server allows AI agents to interact with Telegram (sending/editing messages, searching chats, managing drafts, downloading media) using the [MTProto](https://core.telegram.org/mtproto).

---
<details>
<summary><strong>Table of Contents</strong></summary>

- [🚀 Quick Start (Docker)](#-quick-start-docker-recommended)
- [📦 Installation Variants](#-installation-variants)
  - [Variant A: Docker Compose](#variant-a-docker-compose-recommended)
  - [Variant B: Setup Wizard](#variant-b-setup-wizard-local-postgresql)
  - [Variant C: Direct Mode](#variant-c-direct-mode-legacy-sqlite)
- [🖥️ Web Dashboard](#️-web-dashboard)
- [⚙️ MCP Client Configuration](#️-mcp-client-configuration)
- [🧰 Available Tools](#-available-tools)
- [🤝 Contributing](#-contributing)
- [📝 License](#-license)

</details>

---

## 🚀 Quick Start (Docker) [Recommended]

The fastest way to get started with **v0.3.0** is using Docker Compose. It includes a PostgreSQL database for multi-terminal support and a Web Dashboard for easy authentication.

1. **Clone and Setup Env**:
   ```bash
   cp .env.example .env
   # Edit .env with your API_ID and API_HASH from my.telegram.org
   ```

2. **Start the Stack**:
   ```bash
   docker-compose up -d
   ```

3. **Authenticate via Browser**:
   Open **`http://localhost:8765`** in your browser. Enter your phone number and the verification code sent to your Telegram app.

---

## 📦 Installation Variants

### Variant A: Docker Compose (Recommended)
**Best for**: Permanent background service with multi-terminal support.
- **Backend**: PostgreSQL (containerized).
- **Interface**: Web Dashboard.
- **Persistence**: Automatic via Docker Volumes.
- **Command**: `docker-compose up -d`

### Variant B: Setup Wizard (Local PostgreSQL)
**Best for**: Running locally on your OS without Docker, while still supporting multiple MCP clients.
- **Backend**: PostgreSQL (local).
- **Interface**: Interactive Terminal.
- **Command**: 
  ```bash
  uv tool install mcp-telegram --force
  mcp-telegram setup
  ```

### Variant C: Direct Mode (Legacy SQLite)
**Best for**: Quick one-off tests without a database server.
- **Backend**: SQLite (local file).
- **Interface**: Interactive Terminal.
- **Limit**: Only ONE MCP client can connect at a time (SQLite lock).
- **Command**: 
  ```bash
  uv tool install mcp-telegram --force
  mcp-telegram login
  mcp-telegram start
  ```

---

## 🖥️ Web Dashboard

Version 0.3.0 introduces a **Web Dashboard** accessible at the daemon port (default `8765`).

- **Real-time Status**: Monitor connection and authorization state.
- **Interactive Auth**: Login with phone, code, and 2FA password directly from your browser.
- **Service Control**: Restart the daemon with a single click.
- **Log Stream**: View recent activity and connection logs.

---

## ⚙️ MCP Client Configuration

Add the following to your `claude_desktop_config.json` or Cursor settings:

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

---

## 🧰 Available Tools

| Tool | Description |
|------|-------------|
| `send_message` | ✉️ Send text or files to users/groups/channels |
| `edit_message` | ✏️ Edit previously sent messages |
| `search_dialogs` | 🔎 Find chats by name or username |
| `get_messages` | 📜 Retrieve chat history |
| `get_draft` | 📋 View current message drafts |
| `media_download` | 📸 Download photos/videos from messages |

---

## 🤝 Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

<div align="center">
  <p>Originally made by <a href="https://x.com/dryeab">Yeabsira Driba</a></p>
  <p>v0.3.0 enhancements by <a href="https://github.com/imdeniil">imdeniil</a></p>
</div>
