# Text-to-SQL Agent with Column-Level Access Control

An MCP server that provides natural language to SQL capabilities with column-level access control. Built as a learning project to explore agentic patterns with Claude.

## What it does

- Converts natural language questions to BigQuery SQL
- Enforces column-level access control using deterministic SQL rewriting
- Parses SQL with sqlglot for exact column detection (no substring matching)
- Automatically finds join paths via foreign key detection

## Architecture

```
User Query: "What are total sales?"
                │
                ▼
┌─────────────────────────────────────────────────────────────┐
│  Main LLM (generates SQL)                                   │
│  Output: SELECT SUM(amount) FROM sales                      │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  Access Control Check (sqlglot)                             │
│  • Parse SQL, extract column references                     │
│  • Check: is sales.amount in restricted columns?            │
│  • YES → apply rule                                         │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  Deterministic SQL Rewriting                                │
│  • Find join path: sales → ae_territories → account_execs   │
│  • Add JOINs and WHERE clause                               │
│  Output:                                                    │
│    SELECT SUM(amount) FROM sales                            │
│    INNER JOIN ae_territories ON sales.territory = ...       │
│    INNER JOIN account_execs ON ae_territories.ae_id = ...   │
│    WHERE account_execs.ae_email = 'bob@acme.com'            │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
                ┌───────────┐
                │ BigQuery  │
                └───────────┘
```

## Key Improvements

- **No LLM in enforcement path** - SQL rewriting is deterministic, not LLM-based
- **Exact column matching** - `amount` doesn't match `amount_ytd` (uses SQL parser)
- **Automatic FK detection** - Finds join paths by analyzing column names
- **Multi-hop joins** - Supports `sales → territories → account_execs`

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

### Standalone Chat Mode (Requires Claude Code)
```bash
python cli.py chat
```

**Note:** Chat mode uses the [Claude Agent SDK](https://docs.anthropic.com/en/docs/agents-and-tools/claude-agent-sdk) which requires [Claude Code](https://claude.com/code) to be installed.

### As MCP Server (for Claude Desktop)

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "text2sql": {
      "command": "python",
      "args": ["/path/to/server.py"]
    }
  }
}
```

## Setting Up Access Control

### 1. Configure dataset and users table
```
> configure dataset myproject.mydataset
> configure users_table account_execs
```

### 2. Detect foreign keys (or set manually)
```
> detect_foreign_keys
Detected:
  sales.territory -> ae_territories.territory
  ae_territories.ae_id -> account_execs.ae_id

> set_foreign_key sales territory ae_territories territory  # if needed
```

### 3. Set access rule
```
> set_access_rule sales.amount account_execs.ae_email
Access rule set for 'sales.amount':
  Controlled by: account_execs.ae_email
  User field: ae_email
  Join path: sales.territory = ae_territories.territory -> ae_territories.ae_id = account_execs.ae_id
```

### 4. Test as a user
```
> manage_identity set bob@acme.com
Now simulating: Bob (ae_email: bob@acme.com)

> what are total sales?
[Access control applied: sales.amount filtered by account_execs.ae_email='bob@acme.com']
SQL: SELECT SUM(amount) FROM sales
     INNER JOIN ae_territories ON sales.territory = ae_territories.territory
     INNER JOIN account_execs ON ae_territories.ae_id = account_execs.ae_id
     WHERE account_execs.ae_email = 'bob@acme.com'
```

## Tools Available

| Tool | Purpose |
|------|---------|
| `read_context` | Read schema/examples scratchpad |
| `write_context` | Add to scratchpad |
| `run_query` | Execute SQL with access control |
| `preview_query` | Preview access control without executing |
| `list_schema_columns` | List all tables and columns |
| `detect_foreign_keys` | Auto-detect FK relationships |
| `set_foreign_key` | Manually set FK relationship |
| `set_access_rule` | Add access control rule |
| `list_access_rules` | Show all rules |
| `remove_access_rule` | Remove a rule |
| `manage_identity` | Switch simulated user |
| `configure_dataset` | Set BigQuery dataset |

## How It Works

1. **Admin sets rule**: `sales.amount` controlled by `account_execs.ae_email`
2. **FK detection**: System finds path from `sales` to `account_execs`
3. **Query arrives**: `SELECT SUM(amount) FROM sales`
4. **Column check**: sqlglot parses SQL, finds `sales.amount` (exact match)
5. **SQL rewrite**: Adds JOINs + WHERE deterministically (no LLM)
6. **Execute**: Modified SQL runs against BigQuery

## Files

| File | Purpose |
|------|---------|
| `server.py` | MCP server with sqlglot-based access control |
| `cli.py` | CLI (init, serve, chat) |
| `config.json` | Access rules and FK definitions |
| `context.md` | Scratchpad for schema/examples |

## Limitations

This is a **prototype for learning**, not production-ready:

- FK detection uses naming conventions (may miss relationships)
- Single-value filters only (no IN clauses)
- No support for subqueries in restricted column check

For production, consider:
- BigQuery row-level security policies
- Views per role
- Explicit FK metadata

## License

MIT
