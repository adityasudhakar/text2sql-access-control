# Text-to-SQL Agent with Column-Level Access Control

An MCP server that provides natural language to SQL capabilities with column-level access control. Built as a learning project to explore agentic patterns with Claude.

## What it does

- Converts natural language questions to BigQuery SQL
- Enforces column-level access control using an enforcer LLM
- Can run standalone or as an MCP server for Claude Desktop/other agents

## Architecture

```
User Input
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Main LLM (generates SQL from natural language)             │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  Access Control Check                                        │
│  • Does SQL contain any restricted column names?            │
│  • If NO → execute directly                                 │
│  • If YES → pass to enforcer                                │
└─────────────────────┬───────────────────────────────────────┘
                      │ (only if restricted columns detected)
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  Enforcer LLM (rewrites SQL)                                │
│  • Only sees: SQL + rule (e.g., "add WHERE employee_id=X")  │
│  • Never sees: user input (prevents prompt injection)       │
└─────────────────────┬───────────────────────────────────────┘
                      │
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

### Standalone Chat Mode (Requires Claude Code)
```bash
python cli.py chat
```

**Note:** Chat mode uses the [Claude Agent SDK](https://docs.anthropic.com/en/docs/agents-and-tools/claude-agent-sdk) which requires [Claude Code](https://claude.com/code) to be installed.

Talk naturally:
- "users are in the employees table"
- "restrict salary to self-only access"
- "test as John Smith"
- "what is the average salary?" (will be filtered to John's own data)

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
# Admin sets up restriction
[Admin]> restrict salary so users can only see their own
→ Setting up restriction on employees.salary (self_only)
→ User ID column: employee_id

# Admin sees everything
[Admin]> what are all salaries?
→ Returns all employee salaries

# Switch to regular user
[Admin]> test as John Smith
→ Now simulating: John Smith (id: 42)

# Same query - enforcer rewrites SQL
[John Smith]> what are all salaries?
→ [Access control applied: salary restricted to employee_id='42']
→ Returns only John's salary
```

## How Column-Level Access Control Works

1. **Admin describes what to restrict** in plain English (e.g., "salary data")
2. **Claude suggests matching columns** from the schema
3. **Admin picks exact columns** to restrict
4. **Detection**: Exact string match of column name in SQL
5. **Enforcement**: Enforcer LLM rewrites SQL (only sees SQL + rule, never user input)

### Rule Types

- **self_only**: Users can only see their own data
  - Requires: `user_id_column` (which column identifies the owner)

- **role_based**: Only certain roles can access
  - Requires: `allowed_roles` (list of permitted roles)

## Tools Available

| Tool | Purpose |
|------|---------|
| `read_context` | Read schema/examples scratchpad |
| `write_context` | Add to scratchpad |
| `run_query` | Execute SQL with access control |
| `preview_query` | Preview access control without executing |
| `suggest_restricted_columns` | Suggest columns matching a description |
| `set_column_restriction` | Add restriction to a column |
| `list_restrictions` | Show all restrictions |
| `remove_restriction` | Remove a restriction |
| `manage_identity` | Switch simulated user |
| `configure_dataset` | Set BigQuery dataset |

## Files

| File | Purpose |
|------|---------|
| `server.py` | MCP server with tools + enforcer |
| `cli.py` | CLI (init, serve, chat) |
| `config.json` | Column restrictions |
| `context.md` | Scratchpad for schema/examples |

## Limitations

This is a **prototype for learning**, not production-ready:

- Column detection is simple string matching (could match partial names)
- Enforcer LLM might make mistakes on complex queries
- No caching of enforcer rewrites

For production, consider:
- SQL parser like `sqlglot` for precise column detection
- BigQuery column-level security policies
- Views per role

## License

MIT
