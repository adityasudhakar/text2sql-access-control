"""
Text-to-SQL MCP Server with Column-Level Access Control

An MCP server that provides text-to-SQL capabilities with column-level access control.
Uses an enforcer LLM to rewrite SQL when sensitive columns are detected.
"""

import os
import json
from typing import Any
from google.cloud import bigquery
from anthropic import Anthropic
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load environment
load_dotenv()

# Initialize FastMCP server
mcp = FastMCP("text2sql")

# Configuration paths
CONFIG_FILE = "config.json"
CONTEXT_FILE = "context.md"
SERVICE_ACCOUNT_FILE = "service_account.json"

# Global state
_bq_client = None
_config = None
_current_identity = None
_anthropic_client = None


def _get_anthropic() -> Anthropic:
    """Get or create Anthropic client"""
    global _anthropic_client
    if _anthropic_client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")
        _anthropic_client = Anthropic(api_key=api_key)
    return _anthropic_client


def _get_config() -> dict:
    """Load configuration from config.json"""
    global _config
    if _config is None:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                _config = json.load(f)
        else:
            _config = {}
    return _config


def _save_config(config: dict):
    """Save configuration to config.json"""
    global _config
    _config = config
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def _get_bq_client() -> bigquery.Client:
    """Get or create BigQuery client"""
    global _bq_client
    if _bq_client is None:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = SERVICE_ACCOUNT_FILE
        _bq_client = bigquery.Client()
    return _bq_client


def _load_schema(dataset: str) -> dict:
    """Load schema from BigQuery dataset"""
    client = _get_bq_client()
    schema = {}
    try:
        tables = list(client.list_tables(dataset))
        for table in tables:
            full_table_id = f"{dataset}.{table.table_id}"
            table_ref = client.get_table(full_table_id)
            schema[table.table_id] = {
                "columns": [{"name": f.name, "type": f.field_type} for f in table_ref.schema]
            }
    except Exception as e:
        return {"error": str(e)}
    return schema


def _get_all_columns(dataset: str) -> list:
    """Get all column names from all tables in the dataset"""
    schema = _load_schema(dataset)
    if "error" in schema:
        return []
    columns = []
    for table_name, table_info in schema.items():
        for col in table_info["columns"]:
            columns.append({
                "table": table_name,
                "column": col["name"],
                "type": col["type"],
                "full_name": f"{table_name}.{col['name']}"
            })
    return columns


def _check_restricted_columns(sql: str, config: dict) -> list:
    """
    Check if SQL contains any restricted columns.
    Returns list of matched restrictions.
    """
    restricted_columns = config.get("restricted_columns", {})
    matches = []

    sql_lower = sql.lower()
    for column_name, rule in restricted_columns.items():
        # Exact match on column name
        if column_name.lower() in sql_lower:
            matches.append({
                "column": column_name,
                "rule": rule
            })

    return matches


def _enforcer_rewrite(sql: str, restriction: dict, current_user: dict, config: dict) -> dict:
    """
    Use enforcer LLM to rewrite SQL with access control.
    The enforcer only sees the SQL and the rule - no user input.
    """
    column = restriction["column"]
    rule = restriction["rule"]

    # Build the enforcement instruction
    if rule["type"] == "self_only":
        user_id_column = rule["user_id_column"]
        user_id_value = current_user.get(rule.get("user_id_field", "id"))

        if user_id_value is None:
            return {
                "success": False,
                "error": f"Cannot determine user ID for access control",
                "sql": sql
            }

        instruction = f"""Rewrite this SQL to add an access control filter.

RULE: The column '{column}' is restricted. Users can only see their own data.
FILTER TO ADD: {user_id_column} = '{user_id_value}'

Original SQL:
{sql}

Requirements:
1. Add a WHERE clause (or AND condition) to filter by {user_id_column} = '{user_id_value}'
2. Keep the query logic otherwise identical
3. Return ONLY the modified SQL, no explanations

Modified SQL:"""

    elif rule["type"] == "role_based":
        allowed_roles = rule.get("allowed_roles", [])
        user_role = current_user.get(rule.get("role_field", "role"))

        if user_role not in allowed_roles:
            return {
                "success": False,
                "error": f"Access denied: role '{user_role}' cannot access '{column}'",
                "sql": sql
            }
        # Role is allowed, no rewrite needed
        return {"success": True, "sql": sql, "rules_applied": []}

    else:
        return {
            "success": False,
            "error": f"Unknown rule type: {rule['type']}",
            "sql": sql
        }

    # Call enforcer LLM
    try:
        client = _get_anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": instruction}]
        )

        modified_sql = response.content[0].text.strip()
        # Clean up any markdown
        if modified_sql.startswith("```"):
            modified_sql = modified_sql.split("\n", 1)[1]
        if modified_sql.endswith("```"):
            modified_sql = modified_sql.rsplit("```", 1)[0]
        modified_sql = modified_sql.strip()

        return {
            "success": True,
            "sql": modified_sql,
            "original_sql": sql,
            "rules_applied": [f"{column}: restricted to {user_id_column}='{user_id_value}'"]
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Enforcer error: {e}",
            "sql": sql
        }


def _apply_access_control(sql: str, user: dict, config: dict) -> dict:
    """
    Apply column-level access control to SQL query.
    1. Check if SQL contains any restricted columns
    2. If yes, use enforcer LLM to rewrite
    """
    if user is None:
        # Admin mode - no restrictions
        return {"sql": sql, "rules_applied": [], "access_restricted": False}

    # Check for restricted columns
    matches = _check_restricted_columns(sql, config)

    if not matches:
        return {"sql": sql, "rules_applied": [], "access_restricted": False}

    # Apply each restriction via enforcer
    current_sql = sql
    all_rules_applied = []

    for match in matches:
        result = _enforcer_rewrite(current_sql, match, user, config)

        if not result["success"]:
            return {
                "sql": sql,
                "error": result["error"],
                "access_denied": True
            }

        current_sql = result["sql"]
        all_rules_applied.extend(result.get("rules_applied", []))

    return {
        "sql": current_sql,
        "original_sql": sql,
        "rules_applied": all_rules_applied,
        "access_restricted": bool(all_rules_applied)
    }


# ============================================
# MCP Tools
# ============================================

@mcp.tool()
def read_context() -> str:
    """
    Read the scratchpad file containing schema information, learned values, and Q-SQL pairs.
    Use this to understand the database structure and see examples of correct SQL queries.
    """
    if os.path.exists(CONTEXT_FILE):
        with open(CONTEXT_FILE, "r") as f:
            return f.read()
    return "No context file exists yet. Use write_context to add information."


@mcp.tool()
def write_context(content: str, section: str = "notes") -> str:
    """
    Append content to the scratchpad file.

    Args:
        content: The content to append
        section: Which section to append to (schema, learned_values, sql_examples, notes)
    """
    valid_sections = ["schema", "learned_values", "sql_examples", "notes"]
    if section not in valid_sections:
        return f"Invalid section. Must be one of: {valid_sections}"

    existing = ""
    if os.path.exists(CONTEXT_FILE):
        with open(CONTEXT_FILE, "r") as f:
            existing = f.read()

    section_header = f"## {section.replace('_', ' ').title()}"
    if section_header not in existing:
        existing += f"\n\n{section_header}\n"

    lines = existing.split('\n')
    new_lines = []
    in_section = False
    content_added = False

    for i, line in enumerate(lines):
        new_lines.append(line)
        if line.strip() == section_header:
            in_section = True
        elif in_section and line.startswith("## "):
            new_lines.insert(-1, content)
            content_added = True
            in_section = False

    if not content_added:
        new_lines.append(content)

    with open(CONTEXT_FILE, "w") as f:
        f.write('\n'.join(new_lines))

    return f"Added to {section} section."


@mcp.tool()
def run_query(sql: str) -> str:
    """
    Execute a SQL query against BigQuery with access control applied.
    If the query touches restricted columns, it will be automatically rewritten
    by the enforcer to apply access control rules.

    Args:
        sql: The SQL query to execute
    """
    global _current_identity
    config = _get_config()

    if not config.get("dataset"):
        return "Error: No dataset configured. Run 'text2sql init' first."

    # Apply access control
    result = _apply_access_control(sql, _current_identity, config)

    if result.get("access_denied"):
        return f"Access Denied: {result.get('error')}"

    if result.get("error"):
        return f"Error applying access control: {result['error']}"

    controlled_sql = result["sql"]

    # Execute query
    try:
        client = _get_bq_client()
        query_result = client.query(controlled_sql).result()
        rows = [dict(row) for row in query_result]

        output = []
        if result.get("access_restricted"):
            output.append(f"[Access control applied: {', '.join(result['rules_applied'])}]")
        output.append(f"SQL: {controlled_sql}")
        output.append("")

        if rows:
            headers = list(rows[0].keys())
            output.append(" | ".join(str(h) for h in headers))
            output.append("-" * 50)
            for row in rows[:20]:
                output.append(" | ".join(str(row[h]) for h in headers))
            if len(rows) > 20:
                output.append(f"... and {len(rows) - 20} more rows")
        else:
            output.append("No results found.")

        return "\n".join(output)
    except Exception as e:
        return f"SQL: {controlled_sql}\n\nError: {e}"


@mcp.tool()
def preview_query(sql: str) -> str:
    """
    Preview how a SQL query would be modified by access control WITHOUT executing it.
    Use this to verify access control is working correctly before running queries.

    Args:
        sql: The SQL query to preview
    """
    global _current_identity
    config = _get_config()

    if not config.get("dataset"):
        return "Error: No dataset configured."

    result = _apply_access_control(sql, _current_identity, config)

    output = []
    output.append(f"Original SQL:\n{sql}")
    output.append("")

    if _current_identity:
        output.append(f"Current user: {_current_identity.get('name', 'Unknown')}")
        output.append(f"User attributes: {json.dumps(_current_identity)}")
    else:
        output.append("Current user: Admin (no restrictions)")

    output.append("")

    if result.get("access_denied"):
        output.append(f"ACCESS DENIED: {result.get('error')}")
    elif result.get("access_restricted"):
        output.append("Modified SQL (with access control):")
        output.append(result["sql"])
        output.append("")
        output.append("Rules applied:")
        for rule in result["rules_applied"]:
            output.append(f"  - {rule}")
    else:
        output.append("No access control modifications needed.")

    return "\n".join(output)


@mcp.tool()
def suggest_restricted_columns(description: str) -> str:
    """
    Given a natural language description of what should be restricted,
    suggest matching columns from the schema.

    Args:
        description: Natural language description like "salary data" or "personal information"
    """
    config = _get_config()
    dataset = config.get("dataset")

    if not dataset:
        return "Error: No dataset configured."

    columns = _get_all_columns(dataset)
    if not columns:
        return "Error: Could not load schema."

    # Format columns for Claude
    columns_str = "\n".join([f"- {c['full_name']} ({c['type']})" for c in columns])

    prompt = f"""Given this description of sensitive data: "{description}"

And these available columns:
{columns_str}

Which columns match what the user wants to restrict?
Return a JSON array of column names that match, e.g.: ["employees.salary", "employees.ssn"]
Only include exact column names from the list above.
Return ONLY the JSON array, no explanation."""

    try:
        client = _get_anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )

        result = response.content[0].text.strip()
        # Parse to validate
        suggested = json.loads(result)

        output = [f"Based on '{description}', these columns might be sensitive:"]
        for col in suggested:
            output.append(f"  - {col}")
        output.append("")
        output.append("To restrict a column, use: set_column_restriction")

        return "\n".join(output)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_column_restriction(
    column_name: str,
    rule_type: str,
    user_id_column: str = None,
    user_id_field: str = "id",
    allowed_roles: str = None,
    role_field: str = "role"
) -> str:
    """
    Set access control restriction on a specific column.

    Args:
        column_name: The column to restrict (e.g., "employees.salary")
        rule_type: Either "self_only" (users see own data) or "role_based" (only certain roles)
        user_id_column: For self_only: which column identifies the user (e.g., "employee_id")
        user_id_field: For self_only: which field in user record has their ID (default: "id")
        allowed_roles: For role_based: JSON array of allowed roles (e.g., '["admin", "hr"]')
        role_field: For role_based: which field in user record has their role (default: "role")
    """
    config = _get_config()
    restricted_columns = config.get("restricted_columns", {})

    if rule_type == "self_only":
        if not user_id_column:
            return "Error: user_id_column required for 'self_only' rule"

        restricted_columns[column_name] = {
            "type": "self_only",
            "user_id_column": user_id_column,
            "user_id_field": user_id_field
        }

    elif rule_type == "role_based":
        if not allowed_roles:
            return "Error: allowed_roles required for 'role_based' rule"

        try:
            roles = json.loads(allowed_roles)
        except:
            return "Error: allowed_roles must be valid JSON array"

        restricted_columns[column_name] = {
            "type": "role_based",
            "allowed_roles": roles,
            "role_field": role_field
        }

    else:
        return f"Error: Unknown rule_type '{rule_type}'. Use 'self_only' or 'role_based'."

    config["restricted_columns"] = restricted_columns
    _save_config(config)

    return f"Restriction set on '{column_name}': {rule_type}"


@mcp.tool()
def list_restrictions() -> str:
    """List all configured column restrictions."""
    config = _get_config()
    restricted_columns = config.get("restricted_columns", {})

    if not restricted_columns:
        return "No column restrictions configured."

    output = ["Configured column restrictions:"]
    for column, rule in restricted_columns.items():
        output.append(f"\n{column}:")
        output.append(f"  Type: {rule['type']}")
        if rule['type'] == 'self_only':
            output.append(f"  User ID column: {rule['user_id_column']}")
            output.append(f"  User ID field: {rule.get('user_id_field', 'id')}")
        elif rule['type'] == 'role_based':
            output.append(f"  Allowed roles: {rule['allowed_roles']}")
            output.append(f"  Role field: {rule.get('role_field', 'role')}")

    return "\n".join(output)


@mcp.tool()
def remove_restriction(column_name: str) -> str:
    """
    Remove access control restriction from a column.

    Args:
        column_name: The column to unrestrict
    """
    config = _get_config()
    restricted_columns = config.get("restricted_columns", {})

    if column_name not in restricted_columns:
        return f"No restriction found for '{column_name}'."

    del restricted_columns[column_name]
    config["restricted_columns"] = restricted_columns
    _save_config(config)

    return f"Restriction removed from '{column_name}'."


@mcp.tool()
def manage_identity(
    operation: str,
    user_identifier: str = None
) -> str:
    """
    Manage the current simulated user identity for testing access control.

    Args:
        operation: One of 'get', 'set', 'clear', 'list_users'
        user_identifier: Name, username, or email to identify the user (for set)
    """
    global _current_identity
    config = _get_config()

    if operation == "get":
        if _current_identity:
            return f"Current identity: {json.dumps(_current_identity, indent=2)}"
        return "Current identity: Admin (full access, no restrictions)"

    elif operation == "clear":
        _current_identity = None
        return "Identity cleared. Now operating as Admin with full access."

    elif operation == "list_users":
        users_table = config.get("users_table")
        if not users_table:
            return "No users table configured."

        dataset = config.get("dataset")
        try:
            client = _get_bq_client()
            query = f"SELECT * FROM `{dataset}.{users_table}`"
            result = client.query(query).result()
            users = [dict(row) for row in result]

            if not users:
                return "No users found in users table."

            output = ["Available users:"]
            for user in users:
                output.append(f"  - {json.dumps(user)}")
            return "\n".join(output)
        except Exception as e:
            return f"Error listing users: {e}"

    elif operation == "set":
        if not user_identifier:
            return "Error: user_identifier required for 'set' operation"

        users_table = config.get("users_table")
        if not users_table:
            return "No users table configured."

        dataset = config.get("dataset")
        try:
            client = _get_bq_client()
            query = f"SELECT * FROM `{dataset}.{users_table}`"
            result = client.query(query).result()
            users = [dict(row) for row in result]

            for user in users:
                for key, value in user.items():
                    if str(value).lower() == user_identifier.lower():
                        _current_identity = user
                        return f"Now simulating user: {json.dumps(user, indent=2)}"

            return f"User '{user_identifier}' not found. Use 'list_users' to see available users."
        except Exception as e:
            return f"Error finding user: {e}"

    else:
        return f"Unknown operation '{operation}'. Use: get, set, clear, list_users"


@mcp.tool()
def configure_dataset(dataset: str = None, users_table: str = None) -> str:
    """
    Configure the BigQuery dataset and users table.

    Args:
        dataset: BigQuery dataset in format 'project.dataset' (optional, shows current if not provided)
        users_table: Name of the table containing user information (optional)
    """
    config = _get_config()

    if dataset is None and users_table is None:
        output = ["Current configuration:"]
        output.append(f"  Dataset: {config.get('dataset', 'NOT SET')}")
        output.append(f"  Users table: {config.get('users_table', 'NOT SET')}")
        return "\n".join(output)

    if dataset:
        try:
            schema = _load_schema(dataset)
            if "error" in schema:
                return f"Error: Could not access dataset '{dataset}': {schema['error']}"
            config["dataset"] = dataset
            _save_config(config)

            schema_str = f"# Schema for {dataset}\n\n"
            for table, info in schema.items():
                schema_str += f"## {table}\n"
                for col in info["columns"]:
                    schema_str += f"- {col['name']} ({col['type']})\n"
                schema_str += "\n"

            with open(CONTEXT_FILE, "w") as f:
                f.write(schema_str)

            return f"Dataset set to '{dataset}'. Schema written to context.md. Found {len(schema)} tables."
        except Exception as e:
            return f"Error: {e}"

    if users_table:
        if not config.get("dataset"):
            return "Error: Configure dataset first."

        schema = _load_schema(config["dataset"])
        if users_table not in schema:
            return f"Error: Table '{users_table}' not found. Available: {', '.join(schema.keys())}"

        config["users_table"] = users_table
        _save_config(config)
        return f"Users table set to '{users_table}'."


# ============================================
# Entry point
# ============================================

def main():
    """Run the MCP server"""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    import sys
    # Support --mcp flag (for consistency, but always runs as MCP)
    if len(sys.argv) > 1 and sys.argv[1] == "--mcp":
        main()
    else:
        main()
