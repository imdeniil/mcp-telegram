---
id: "001"
status: "pending"
priority: "p2"
description: "Fix log output in Web Dashboard"
dependencies: []
created_at: "2026-03-26"
---

# Fix Log Output in Web Dashboard

## Problem
The current log output in `src/mcp_telegram/web/index.html` is a mock/placeholder. It does not stream real-time logs from the daemon.

## Goal
Implement a real-time log streaming mechanism (e.g., via Server-Sent Events or WebSockets) from the FastAPI daemon to the Web Dashboard.

## Proposed Solution
1. Add a custom logging handler in `src/mcp_telegram/daemon.py` that pushes logs to a queue.
2. Create a `/api/logs` endpoint using `EventSource` (SSE) to stream logs from the queue.
3. Update `index.html` to connect to this endpoint and display logs dynamically.
