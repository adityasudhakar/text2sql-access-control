# CLAUDE.md

This file provides guidance for AI assistants working in this repository.

## Project Overview

This is a **text-to-SQL MCP (Model Context Protocol) server** with column-level access control. It converts natural language questions into BigQuery SQL while enforcing data access restrictions based on user identity and role.

**Key capabilities:**
- Natural language → BigQuery SQL via Claude
- Column-level access control (self_only and role_based restriction types)
- Prompt injection prevention via isolated enforcer LLM
- Two operating modes: standalone interactive chat and MCP server

## Repository Structure

```
text2sql-access-control/
├── cli.py              # CLI entry point (init, serve, chat commands)
├── server.py           # Core MCP server with access control logic (716 lines)
├── requirements.txt    # Python dependencies
├── config.json         # Column restriction configuration (persisted state)
├── context.md          # Auto-populated BigQuery schema scratchpad
└── README.md           # User-facing documentation
```

All file paths in the code are anchored to the script directory (not `cwd`). The files `service_account.json` and `.env` are gitignored and must be created manually.

## Technology Stack

- **Language:** Python 3
- **LLM:** Claude (`claude-sonnet-4-20250514`) via `anthropic` SDK
- **MCP framework:** FastMCP (`mcp>=1.0.0`)
- **Chat mode:** Claude Agent SDK (`claude-agent-sdk>=0.1.0`)
- **Database:** Google BigQuery (`google-cloud-bigquery>=3.14.0`)
- **Config:** `python-dotenv`, JSON files

## Development Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Place BigQuery credentials in the project root
cp /path/to/your-service-account.json service_account.json

# Set your Anthropic API key
export ANTHROPIC_API_KEY=sk-ant-...
# or add to .env file

# Configure your BigQuery dataset (interactive wizard)
python cli.py init
```

## Running the Project

```bash
python cli.py init    # One-time setup: configure dataset & credentials
python cli.py serve   # Run as MCP server over stdio (for Claude Desktop etc.)
python cli.py chat    # Interactive standalone chat mode
```

**MCP config for Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "text2sql": {
      "command": "python",
      "args": ["/absolute/path/to/server.py"],
      "env": { "ANTHROPIC_API_KEY": "sk-ant-..." }
    }
  }
}
```

## Architecture

### Access Control Flow

```
User natural language query
        ↓
Main LLM (Claude) generates SQL
        ↓
Does SQL reference restricted columns?
   NO  → Execute directly
   YES → Enforcer LLM (sees ONLY the SQL + rule, never user input)
             ↓
         Rewrites SQL with access filters applied
        ↓
Execute modified SQL on BigQuery
```

**Security invariant:** The enforcer LLM is isolated from user input to prevent prompt injection attacks. It receives only the generated SQL and the restriction rule.

### Config Schema (`config.json`)

```json
{
  "dataset": "project-id.dataset_name",
  "users_table": "users",
  "restricted_columns": {
    "table.column": {
      "type": "self_only",
      "user_id_column": "employee_id",
      "user_id_field": "id"
    },
    "table.other_column": {
      "type": "role_based",
      "allowed_roles": ["admin", "hr"],
      "role_field": "role"
    }
  }
}
```

**Restriction types:**
- `self_only` — Filters rows to `WHERE user_id_column = current_user.user_id_field`
- `role_based` — Blocks access unless `current_user.role_field` is in `allowed_roles`

### MCP Tools (10 total in `server.py`)

| Tool | Purpose |
|------|---------|
| `read_context()` | Read schema/scratchpad file |
| `write_context(content, section)` | Append to scratchpad (sections: `schema`, `learned_values`, `sql_examples`, `notes`) |
| `run_query(sql)` | Execute SQL with access control (max 20 rows returned) |
| `preview_query(sql)` | Preview access control rewrites without executing |
| `suggest_restricted_columns(description)` | LLM suggests columns matching a description |
| `set_column_restriction(column_name, rule_type, ...)` | Add/update a restriction rule |
| `list_restrictions()` | Display all configured restrictions |
| `remove_restriction(column_name)` | Remove a restriction |
| `manage_identity(operation, user_identifier)` | Get/set/clear current user identity for testing |
| `configure_dataset(dataset, users_table)` | Set BigQuery dataset and users table |

## Code Conventions

### Naming
- Private/internal functions are prefixed with `_` (e.g., `_get_bq_client`, `_apply_access_control`)
- MCP tool functions are decorated with `@mcp.tool()` and have no `_` prefix
- Module-level constants use `UPPERCASE` (e.g., `CONFIG_FILE`, `SCRIPT_DIR`)
- Global state variables use `_underscore_prefix` (e.g., `_bq_client`, `_config`, `_current_identity`)

### Patterns
- **Lazy initialization:** BigQuery and Anthropic clients are created on first use via getter functions (`_get_bq_client()`, `_get_anthropic_client()`)
- **Config persistence:** `config.json` is written to disk after any state change
- **Error handling:** Tool functions return descriptive error strings rather than raising exceptions; callers check for error indicators in the string
- **Path resolution:** All file paths are resolved relative to `SCRIPT_DIR = Path(__file__).parent`, not the working directory
- **Docstrings:** All MCP tools and major functions have docstrings (these are exposed to LLM clients)

### File Modification Rules
- `config.json` — Modified only by MCP tools (`set_column_restriction`, `remove_restriction`, `configure_dataset`). Do not manually edit during a running session.
- `context.md` — Append-only scratchpad. Agents should write to it via `write_context()`, never overwrite unless explicitly clearing.
- `server.py` — Contains all business logic. Changes here affect both MCP server and chat modes.
- `cli.py` — Only handles argument parsing and mode dispatch; keep business logic out of it.

## Key Functions

### `server.py`

- `_apply_access_control(sql)` — Main orchestrator: detects restricted columns and calls enforcer
- `_check_restricted_columns(sql, restricted)` — Scans SQL text for restricted column names (exact match)
- `_enforcer_rewrite(sql, column, rule, users_table)` — Calls Claude to rewrite SQL with access filters
- `_load_schema()` — Fetches all table schemas from BigQuery and writes to `context.md`
- `_get_all_columns()` — Returns `{"table.column": type, ...}` for the configured dataset
- `_get_bq_client()` / `_get_anthropic_client()` — Lazy client initializers

### `cli.py`

- `cmd_init()` — Interactive wizard: prompts for dataset, validates credentials, saves config
- `cmd_serve()` — Starts FastMCP over stdio
- `cmd_chat()` — Spawns `server.py` as subprocess, creates Agent SDK client, runs REPL loop

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | API key for Claude |
| `GOOGLE_APPLICATION_CREDENTIALS` | Auto-set | Path to `service_account.json`; set automatically by the server |

## Testing

There is no automated test suite. Testing is manual:

1. **Unit-style testing:** Use `preview_query(sql)` to verify access control rewrites without executing
2. **Identity simulation:** Use `manage_identity("set", "user@example.com")` to test as different users
3. **Integration testing:** Run `python cli.py chat` for end-to-end natural language → SQL → results testing

When adding new access control logic, test both restriction types (`self_only`, `role_based`) and verify the enforcer correctly rewrites SQL without access to the original user prompt.

## Common Tasks

### Add a new MCP tool
1. Add a new function in `server.py` decorated with `@mcp.tool()`
2. Write a clear docstring — it is visible to LLM clients
3. Return a string result (success message or data); return error strings (not exceptions) for failure cases
4. If the tool modifies config, call `_save_config()` and update `_config` in memory

### Modify access control logic
- Detection logic is in `_check_restricted_columns()` — currently exact string match
- Rewrite logic is in `_enforcer_rewrite()` — the enforcer system prompt is here
- **Never pass original user input to the enforcer prompt** — this is the core security invariant

### Update the model
- The model name `claude-sonnet-4-20250514` appears in both `server.py` (enforcer) and `cli.py` (chat agent)
- Update both locations when changing models

### Add a new restriction type
1. Add handling in `_apply_access_control()` for the new type
2. Add a new enforcer branch in `_enforcer_rewrite()` with appropriate prompt
3. Update `set_column_restriction()` to accept and validate the new type
4. Document the new type in `list_restrictions()` output and in this file

## Limitations & Known Issues

- Column detection is exact string matching — aliased or qualified column names may bypass detection
- No authentication on the MCP server itself — identity management is for testing only
- `context.md` grows unbounded; no pruning mechanism
- Enforcer LLM calls add latency to every query involving restricted columns
- No support for column-level restrictions in subqueries or CTEs (may be bypassed)
