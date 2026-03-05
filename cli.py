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
    system_prompt = """You are a text-to-SQL assistant with access control capabilities.

You have access to these tools (call them by generating SQL or using the appropriate function):

1. read_context() - Read the scratchpad with schema, learned values, and SQL examples
2. write_context(content, section) - Add to scratchpad (sections: schema, learned_values, sql_examples, notes)
3. run_query(sql) - Execute SQL with access control applied
4. preview_query(sql) - Preview SQL with access control WITHOUT executing
5. manage_access_rules(operation, ...) - Manage access dimensions (list/get/set/remove)
6. manage_identity(operation, ...) - Manage simulated user (get/set/clear/list_users)
7. configure_dataset(dataset, users_table) - Configure BigQuery connection

When users ask questions:
- For data questions: Generate SQL and use run_query
- For admin tasks: Use the appropriate management tool
- Check schema first if unsure about table/column names

Always think about:
1. What does the user want?
2. Do I need to check the schema first?
3. What SQL would answer this question?
4. Is access control relevant here?
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
            "description": "Execute SQL query with access control applied",
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
            "name": "manage_access_rules",
            "description": "Manage access control dimensions",
            "input_schema": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": ["list", "get", "set", "remove"]},
                    "dimension_name": {"type": "string"},
                    "user_attribute": {"type": "string"},
                    "source_table": {"type": "string"},
                    "source_column": {"type": "string"},
                    "value_mapping": {"type": "string", "description": "JSON string"}
                },
                "required": ["operation"]
            }
        },
        {
            "name": "manage_identity",
            "description": "Manage simulated user identity",
            "input_schema": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": ["get", "set", "clear", "list_users"]},
                    "user_identifier": {"type": "string"}
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
                    "dataset": {"type": "string"},
                    "users_table": {"type": "string"}
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
        elif name == "manage_access_rules":
            return server.manage_access_rules(
                args.get("operation", "list"),
                args.get("dimension_name"),
                args.get("user_attribute"),
                args.get("source_table"),
                args.get("source_column"),
                args.get("value_mapping")
            )
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
        if config.get("access_dimensions"):
            print(f"Access dimensions: {', '.join(config['access_dimensions'].keys())}")
    else:
        print("\nNo dataset configured. Say 'configure dataset project.dataset'")

    print("\nTalk to me naturally. Examples:")
    print("  'what tables are available?'")
    print("  'users are in sales_people table'")
    print("  'set up geography access control'")
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
