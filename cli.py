#!/usr/bin/env python3
"""
CLI for Text-to-SQL with Access Control

Usage:
    text2sql init      - Initialize configuration (dataset, credentials)
    text2sql serve     - Run as MCP server (for use with Claude Desktop, etc.)
    text2sql chat      - Run standalone interactive chat mode
"""

import sys
import os
import json
import subprocess


def cmd_init():
    """Initialize configuration"""
    print("=" * 60)
    print("  TEXT-TO-SQL ACCESS CONTROL - Setup")
    print("=" * 60)

    config = {}
    config_file = "config.json"

    # Load existing config if present
    if os.path.exists(config_file):
        with open(config_file, "r") as f:
            config = json.load(f)
        print(f"\nExisting configuration found.")
        print(f"  Dataset: {config.get('dataset', 'NOT SET')}")
        print(f"  Users table: {config.get('users_table', 'NOT SET')}")
        update = input("\nUpdate configuration? (y/N): ").strip().lower()
        if update != 'y':
            print("Configuration unchanged.")
            return

    # Get dataset
    print("\n1. BigQuery Dataset")
    print("   Format: project-id.dataset_name")
    default = config.get('dataset', '')
    prompt = f"   Dataset [{default}]: " if default else "   Dataset: "
    dataset = input(prompt).strip() or default

    if not dataset:
        print("Error: Dataset is required.")
        sys.exit(1)

    config['dataset'] = dataset

    # Check for service account
    print("\n2. Service Account")
    sa_file = "service_account.json"
    if os.path.exists(sa_file):
        print(f"   Found: {sa_file}")
    else:
        print(f"   Warning: {sa_file} not found.")
        print("   Place your service account JSON file in this directory.")

    # Save config
    with open(config_file, "w") as f:
        json.dump(config, f, indent=2)

    print("\n" + "=" * 60)
    print("  Setup complete!")
    print("=" * 60)
    print("\nNext steps:")
    print("  1. Run 'text2sql serve' to start the MCP server")
    print("  2. Or run 'text2sql chat' for interactive mode")
    print("\nIn chat/MCP mode, tell the agent:")
    print('  - "users are in the sales_people table"')
    print('  - "set up geography access control"')
    print('  - "test as Alex Brown"')


def cmd_serve():
    """Run as MCP server (stdio transport)"""
    # Import and run the server
    from server import main
    main()


def cmd_chat():
    """Run standalone interactive chat mode with Claude"""
    from anthropic import Anthropic
    from dotenv import load_dotenv
    import server

    # Load .env file if present
    load_dotenv()

    # Get API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set.")
        print("\nSet it via:")
        print("  1. Environment variable: export ANTHROPIC_API_KEY=sk-ant-...")
        print("  2. Or create a .env file with: ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    client = Anthropic(api_key=api_key)
    conversation_history = []

    # Build system prompt with tool descriptions
    system_prompt = """You are a text-to-SQL assistant with column-level access control.

You have access to these tools:

DATA TOOLS:
1. read_context() - Read schema, learned values, and SQL examples
2. write_context(content, section) - Add to scratchpad (schema, learned_values, sql_examples, notes)
3. run_query(sql) - Execute SQL with access control applied automatically
4. preview_query(sql) - Preview SQL with access control WITHOUT executing

ACCESS CONTROL TOOLS:
5. suggest_restricted_columns(description) - Given a description like "salary data", suggest matching columns
6. set_column_restriction(column_name, rule_type, ...) - Set restriction on a column
7. list_restrictions() - List all configured restrictions
8. remove_restriction(column_name) - Remove restriction from a column

IDENTITY & CONFIG:
9. manage_identity(operation, ...) - Manage simulated user (get/set/clear/list_users)
10. configure_dataset(dataset, users_table) - Configure BigQuery connection

HOW ACCESS CONTROL WORKS:
- When a query touches a restricted column, an enforcer LLM rewrites the SQL
- Restrictions are based on exact column name matching
- Rule types: "self_only" (users see own data) or "role_based" (only certain roles)

When users ask questions:
- For data questions: Generate SQL and use run_query
- For admin setup: Use suggest_restricted_columns, then set_column_restriction
- Check schema first if unsure about table/column names
"""

    # Tool definitions for Claude
    tools = [
        {
            "name": "read_context",
            "description": "Read the scratchpad file with schema, learned values, and SQL examples",
            "input_schema": {"type": "object", "properties": {}, "required": []}
        },
        {
            "name": "write_context",
            "description": "Add content to the scratchpad file",
            "input_schema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Content to add"},
                    "section": {"type": "string", "enum": ["schema", "learned_values", "sql_examples", "notes"]}
                },
                "required": ["content", "section"]
            }
        },
        {
            "name": "run_query",
            "description": "Execute SQL query with access control applied automatically",
            "input_schema": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "SQL query to execute"}
                },
                "required": ["sql"]
            }
        },
        {
            "name": "preview_query",
            "description": "Preview SQL with access control WITHOUT executing",
            "input_schema": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "SQL query to preview"}
                },
                "required": ["sql"]
            }
        },
        {
            "name": "suggest_restricted_columns",
            "description": "Given a natural language description of sensitive data, suggest matching columns from the schema",
            "input_schema": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "Description like 'salary data' or 'personal information'"}
                },
                "required": ["description"]
            }
        },
        {
            "name": "set_column_restriction",
            "description": "Set access control restriction on a specific column",
            "input_schema": {
                "type": "object",
                "properties": {
                    "column_name": {"type": "string", "description": "The column to restrict (e.g., 'employees.salary')"},
                    "rule_type": {"type": "string", "enum": ["self_only", "role_based"], "description": "Type of restriction"},
                    "user_id_column": {"type": "string", "description": "For self_only: column that identifies the user"},
                    "user_id_field": {"type": "string", "description": "For self_only: field in user record with their ID (default: 'id')"},
                    "allowed_roles": {"type": "string", "description": "For role_based: JSON array of allowed roles"},
                    "role_field": {"type": "string", "description": "For role_based: field in user record with their role"}
                },
                "required": ["column_name", "rule_type"]
            }
        },
        {
            "name": "list_restrictions",
            "description": "List all configured column restrictions",
            "input_schema": {"type": "object", "properties": {}, "required": []}
        },
        {
            "name": "remove_restriction",
            "description": "Remove access control restriction from a column",
            "input_schema": {
                "type": "object",
                "properties": {
                    "column_name": {"type": "string", "description": "The column to unrestrict"}
                },
                "required": ["column_name"]
            }
        },
        {
            "name": "manage_identity",
            "description": "Manage simulated user identity for testing",
            "input_schema": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": ["get", "set", "clear", "list_users"]},
                    "user_identifier": {"type": "string", "description": "Name, username, or email to identify user (for set)"}
                },
                "required": ["operation"]
            }
        },
        {
            "name": "configure_dataset",
            "description": "Configure BigQuery dataset and users table",
            "input_schema": {
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "BigQuery dataset in format 'project.dataset'"},
                    "users_table": {"type": "string", "description": "Name of table containing user information"}
                },
                "required": []
            }
        }
    ]

    # Tool execution mapping
    def execute_tool(name: str, args: dict) -> str:
        if name == "read_context":
            return server.read_context()
        elif name == "write_context":
            return server.write_context(args.get("content", ""), args.get("section", "notes"))
        elif name == "run_query":
            return server.run_query(args.get("sql", ""))
        elif name == "preview_query":
            return server.preview_query(args.get("sql", ""))
        elif name == "suggest_restricted_columns":
            return server.suggest_restricted_columns(args.get("description", ""))
        elif name == "set_column_restriction":
            return server.set_column_restriction(
                args.get("column_name", ""),
                args.get("rule_type", ""),
                args.get("user_id_column"),
                args.get("user_id_field", "id"),
                args.get("allowed_roles"),
                args.get("role_field", "role")
            )
        elif name == "list_restrictions":
            return server.list_restrictions()
        elif name == "remove_restriction":
            return server.remove_restriction(args.get("column_name", ""))
        elif name == "manage_identity":
            return server.manage_identity(
                args.get("operation", "get"),
                args.get("user_identifier")
            )
        elif name == "configure_dataset":
            return server.configure_dataset(
                args.get("dataset"),
                args.get("users_table")
            )
        else:
            return f"Unknown tool: {name}"

    print("=" * 60)
    print("  TEXT-TO-SQL WITH ACCESS CONTROL")
    print("=" * 60)

    config = server._get_config()
    if config.get("dataset"):
        print(f"\nDataset: {config['dataset']}")
        if config.get("users_table"):
            print(f"Users table: {config['users_table']}")
        if config.get("restricted_columns"):
            print(f"Restricted columns: {', '.join(config['restricted_columns'].keys())}")
    else:
        print("\nNo dataset configured. Say 'configure dataset project.dataset'")

    print("\nTalk to me naturally. Examples:")
    print("  'what tables are available?'")
    print("  'users are in sales_people table'")
    print("  'restrict salary to self-only access'")
    print("  'test as Alex Brown'")
    print("  'what are total sales?'")
    print("  'exit' to quit")
    print()

    while True:
        # Get current identity for prompt
        identity = server._current_identity
        if identity:
            prompt_prefix = f"[{identity.get('name', 'User')}]"
        else:
            prompt_prefix = "[Admin]"

        try:
            user_input = input(f"{prompt_prefix}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        if user_input.lower() == "exit":
            print("Goodbye!")
            break

        # Add user message
        conversation_history.append({"role": "user", "content": user_input})

        # Agent loop
        while True:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                system=system_prompt,
                tools=tools,
                messages=conversation_history
            )

            # Process response
            assistant_content = []
            tool_results = []

            for block in response.content:
                if hasattr(block, "text"):
                    print(f"\n{block.text}")
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    tool_name = block.name
                    tool_input = block.input
                    tool_id = block.id

                    print(f"\n[Tool: {tool_name}]")
                    result = execute_tool(tool_name, tool_input)
                    print(result)

                    assistant_content.append({
                        "type": "tool_use",
                        "id": tool_id,
                        "name": tool_name,
                        "input": tool_input
                    })
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": result
                    })

            # Add assistant message
            conversation_history.append({"role": "assistant", "content": assistant_content})

            # If there were tool calls, add results and continue
            if tool_results:
                conversation_history.append({"role": "user", "content": tool_results})
            else:
                # No more tool calls, done with this turn
                break

            # Check stop reason
            if response.stop_reason == "end_turn":
                break

        print()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "init":
        cmd_init()
    elif command == "serve":
        cmd_serve()
    elif command == "chat":
        cmd_chat()
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
