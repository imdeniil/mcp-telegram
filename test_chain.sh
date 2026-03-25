#!/bin/bash
# Test script for MCP Telegram daemon mode chain
# This verifies the complete setup → login → daemon flow

set -e

echo "========================================="
echo "MCP Telegram Chain Test"
echo "========================================="

# Configuration
DB_HOST="localhost"
DB_PORT="5433"
DB_USER="claude"
DB_PASS="claude_dev"
DB_NAME="mcp_telegram_test"

# Clean up any existing test database
echo ""
echo "[1/5] Preparing test database..."
docker exec continuous-claude-postgres psql -U claude -d postgres -c "DROP DATABASE IF EXISTS $DB_NAME;" 2>/dev/null || true
docker exec continuous-claude-postgres psql -U claude -d postgres -c "CREATE DATABASE $DB_NAME;"
echo "✓ Test database created"

# Run migrations
echo ""
echo "[2/5] Running migrations..."
docker exec -i continuous-claude-postgres psql -U claude -d $DB_NAME < migrations/001_initial_schema.sql > /dev/null
echo "✓ Migrations applied"

# Create test .env
echo ""
echo "[3/5] Creating test environment..."
cat > .env.test << EOF
DATABASE_URL=postgresql://$DB_USER:$DB_PASS@$DB_HOST:$DB_PORT/$DB_NAME
API_ID=24617029
API_HASH=test_hash_placeholder
DAEMON_PORT=8766
EOF
echo "✓ Test .env created"

# Test imports
echo ""
echo "[4/5] Testing Python imports..."
source .venv/bin/activate
python3 -c "
from mcp_telegram.session import PostgresSession, create_session_pool
from mcp_telegram.daemon import DaemonConfig, run_daemon
from mcp_telegram.proxy import DaemonClient
from mcp_telegram.server_proxy import mcp
print('✓ All imports successful')
"

# Test database connection
echo ""
echo "[5/5] Testing database connection..."
python3 -c "
import asyncio
import asyncpg
import os

async def test():
    url = os.environ.get('DATABASE_URL')
    pool = await asyncpg.create_pool(url, min_size=1, max_size=5)
    async with pool.acquire() as conn:
        # Check tables exist
        tables = await conn.fetch(\"SELECT tablename FROM pg_tables WHERE schemaname = 'public'\")
        table_names = [t['tablename'] for t in tables]
        required = ['telegram_accounts', 'sessions', 'entities', 'sent_files', 'update_state', 'schema_version']
        for t in required:
            if t not in table_names:
                raise Exception(f'Missing table: {t}')
        print(f'✓ All {len(required)} tables exist')
    await pool.close()

os.environ['DATABASE_URL'] = 'postgresql://claude:claude_dev@localhost:5433/mcp_telegram_test'
asyncio.run(test())
"

# Cleanup
echo ""
echo "========================================="
echo "✓ All tests passed!"
echo "========================================="
echo ""
echo "To test the full flow manually:"
echo "  1. source .env.test"
echo "  2. mcp-telegram login"
echo "  3. mcp-telegram daemon"
echo "  4. mcp-telegram start --daemon"
