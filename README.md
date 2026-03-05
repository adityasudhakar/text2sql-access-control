# Text-to-SQL Agent with Access Control

An MCP server that provides natural language to SQL capabilities with role-based access control. Built as a learning project to explore agentic patterns with Claude.

## What it does

- Converts natural language questions to BigQuery SQL
- Enforces row-level access control based on user attributes
- Can run standalone or as an MCP server for Claude Desktop/other agents

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  MCP Client (Claude Desktop, Cursor, custom agent)     │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│  MCP Server (server.py)                                 │
│  ┌─────────────────────────────────────────────────┐   │
│  │ Tools:                                           │   │
│  │  • read_context / write_context (scratchpad)    │   │
│  │  • run_query (with access control)              │   │
│  │  • preview_query (verify access control)        │   │
│  │  • manage_access_rules                          │   │
│  │  • manage_identity                              │   │
│  │  • configure_dataset                            │   │
│  └─────────────────────────────────────────────────┘   │
│                         │                               │
│                         ▼                               │
│  ┌─────────────────────────────────────────────────┐   │
│  │ Access Control Layer                             │   │
│  │  • Injects WHERE clauses based on user attrs    │   │
│  │  • Follows FK relationships                     │   │
│  └─────────────────────────────────────────────────┘   │
│                         │                               │
└─────────────────────────┼───────────────────────────────┘
                          ▼
                    ┌───────────┐
                    │ BigQuery  │
                    └───────────┘
```

## Setup

1. **Install dependencies**
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure credentials**
   - Place your BigQuery service account JSON as `service_account.json`
   - Create `.env` with your Anthropic API key:
     ```
     ANTHROPIC_API_KEY=sk-ant-...
     ```

3. **Initialize**
   ```bash
   python cli.py init
   ```

## Usage

### Standalone Chat Mode
```bash
python cli.py chat
```

Talk naturally:
- "users are in the sales_people table"
- "set up geography access control"
- "test as John Smith"
- "what are total sales by region?"

### As MCP Server (for Claude Desktop)

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "text2sql": {
      "command": "python",
      "args": ["/path/to/cli.py", "serve"]
    }
  }
}
```

## Access Control Example

```
# As Admin - see all 100,000 customers
[Admin]> how many customers?
→ 100,000

# Switch to US-only user
[Admin]> test as John Smith
Now simulating: John Smith (geography: US)

# Same query - automatically filtered
[John Smith]> how many customers?
→ 22,482 (US only)
```

## Limitations

This is a **prototype for learning**, not production-ready:

- Access control uses regex-based SQL injection (fragile)
- No proper SQL parsing (breaks on complex queries)
- No query validation before execution

For production, consider:
- SQL parser like `sqlglot`
- BigQuery row-level security policies
- Views per role

## Files

| File | Purpose |
|------|---------|
| `server.py` | MCP server with tools |
| `cli.py` | CLI (init, serve, chat) |
| `config.json` | Access control rules |
| `context.md` | Scratchpad for schema/examples |

## License

MIT
