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

    # Get directory where this script lives
    script_dir = os.path.dirname(os.path.abspath(__file__))

    config = {}
    config_file = os.path.join(script_dir, "config.json")

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
    sa_file = os.path.join(script_dir, "service_account.json")
    if os.path.exists(sa_file):
        print(f"   Found: {sa_file}")
    else:
        print(f"   Warning: service_account.json not found.")
        print(f"   Place your service account JSON file in: {script_dir}")

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
    """Run standalone interactive chat mode using Agent SDK with MCP server"""
    import asyncio
    from dotenv import load_dotenv
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, ResultMessage, AssistantMessage, TextBlock, ToolUseBlock

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

    # Get the path to server.py (in isolated mcp_server directory)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    mcp_server_dir = os.path.join(script_dir, "mcp_server")
    server_path = os.path.join(mcp_server_dir, "server.py")

    print("=" * 60)
    print("  TEXT-TO-SQL WITH ACCESS CONTROL")
    print("  (Using Agent SDK + MCP Server)")
    print("=" * 60)

    # Show current config
    config_file = os.path.join(mcp_server_dir, "config.json")
    config = {}
    if os.path.exists(config_file):
        try:
            with open(config_file, "r") as f:
                content = f.read().strip()
                config = json.loads(content) if content else {}
        except (json.JSONDecodeError, ValueError):
            config = {}
    if config.get("dataset"):
        print(f"\nDataset: {config['dataset']}")
        if config.get("users_table"):
            print(f"Users table: {config['users_table']}")
        if config.get("access_rules"):
            print(f"Access rules: {len(config['access_rules'])} configured")
    else:
        print("\nNo dataset configured. Say 'configure dataset project.dataset'")

    print("\nTalk to me naturally. Examples:")
    print("  'what tables are available?'")
    print("  'users are in sales_people table'")
    print("  'restrict salary to self-only access'")
    print("  'test as Alex Brown'")
    print("  'what are total sales?'")
    print("  'exit' to quit, 'new' for fresh session")
    print()

    # System prompt for the agent
    system_prompt = """You are a text-to-SQL assistant with column-level access control.

When users ask questions:
- For data questions: Generate SQL and use run_query
- For admin setup: Use set_access_rule to restrict columns, list_access_rules to view, remove_access_rule to delete
- Use list_schema_columns to see available tables/columns
- Use detect_foreign_keys to find join relationships
- Use manage_identity to switch simulated users for testing

HOW ACCESS CONTROL WORKS:
- When a query touches a restricted column, JOINs and WHERE clauses are added automatically
- SQL is parsed with sqlglot for exact column matching (not substring)
- Join paths are auto-detected via foreign key relationships

LEARNING & CONTEXT:
- Use read_context to check schema and previous Q-SQL examples
- After successful data queries, use write_context with section="sql_examples" to log useful Q-SQL pairs
- Format: "Q: [user's question] -> SQL: [the query]"
- This helps you learn patterns for this specific dataset
"""

    async def run_chat_session():
        """Run the chat session with persistent client"""
        options = ClaudeAgentOptions(
            mcp_servers={
                "text2sql": {
                    "command": sys.executable,
                    "args": [server_path, "--mcp"],
                    "env": {
                        **os.environ,  # Keep existing env (PATH, etc.)
                        "ANTHROPIC_API_KEY": api_key,
                        "GOOGLE_APPLICATION_CREDENTIALS": os.path.join(mcp_server_dir, "service_account.json"),
                    },
                }
            },
            allowed_tools=["mcp__text2sql__*"],
            system_prompt=system_prompt,
        )

        async with ClaudeSDKClient(options=options) as client:
            while True:
                try:
                    user_input = input("[Admin]> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nGoodbye!")
                    break

                if not user_input:
                    continue

                if user_input.lower() == "exit":
                    print("Goodbye!")
                    break

                if user_input.lower() == "new":
                    print("Starting fresh session...")
                    # Exit this session; outer loop will restart
                    return "restart"

                # Send query and get response
                await client.query(user_input)

                # Process response messages
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                print(block.text)
                            elif isinstance(block, ToolUseBlock):
                                tool_name = block.name.replace('mcp__text2sql__', '')
                                print(f"  [{tool_name}]", end="", flush=True)
                    elif isinstance(message, ResultMessage):
                        if message.subtype == "success":
                            if hasattr(message, 'result') and message.result:
                                print(f"\n{message.result}")
                        elif message.subtype == "error":
                            print(f"\nError: {getattr(message, 'error', 'Unknown error')}")

                print()

        return "exit"

    # Main loop - allows restarting sessions
    while True:
        result = asyncio.run(run_chat_session())
        if result != "restart":
            break


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
