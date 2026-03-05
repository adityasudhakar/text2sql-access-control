"""
Text-to-SQL MCP Server with Access Control

An MCP server that provides text-to-SQL capabilities with role-based access control.
Can be used standalone or as a sub-agent for any MCP-compatible client.
"""

import os
import json
import re
from typing import Any
from google.cloud import bigquery
from mcp.server.fastmcp import FastMCP

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


def _find_table_alias(sql: str, table_name: str, dataset: str) -> str:
    """Find the alias for a table in the SQL query, or return the full table name."""
    full_table = f"`{dataset}.{table_name}`"
    pattern = rf'{re.escape(full_table)}\s+(?:AS\s+)?(\w+)'
    match = re.search(pattern, sql, re.IGNORECASE)
    if match:
        return match.group(1)
    return full_table


def _apply_access_control(sql: str, user: dict, config: dict) -> dict:
    """
    Apply access control to SQL query.
    Returns dict with modified SQL and metadata about applied rules.
    """
    if user is None:
        return {"sql": sql, "rules_applied": [], "access_restricted": False}

    access_dimensions = config.get("access_dimensions", {})
    if not access_dimensions:
        return {"sql": sql, "rules_applied": [], "access_restricted": False}

    dataset = config.get("dataset", "")
    schema = _load_schema(dataset)
    if "error" in schema:
        return {"sql": sql, "rules_applied": [], "error": schema["error"]}

    joins_to_add = []
    where_conditions = []
    rules_applied = []

    # Find tables in query
    tables_in_query = []
    for table in schema.keys():
        if f"`{dataset}.{table}`" in sql or f" {table}" in sql.lower():
            tables_in_query.append(table)

    # Infer foreign keys
    foreign_keys = {}
    for table_name, table_info in schema.items():
        foreign_keys[table_name] = {}
        for col in table_info["columns"]:
            col_name = col["name"].lower()
            if col_name.endswith("_id") and col_name != "id":
                potential_table = col_name[:-3]
                for other_table in schema.keys():
                    other_lower = other_table.lower()
                    if other_lower == potential_table or other_lower == potential_table + "s":
                        foreign_keys[table_name][col["name"]] = f"{other_table}.id"
                        break

    for dim_name, dim_config in access_dimensions.items():
        user_attr = dim_config.get("user_attribute")
        user_value = user.get(user_attr)

        if not user_value or user_value == "All":
            continue

        source_table = dim_config.get("source_table")
        source_column = dim_config.get("source_column")
        value_mapping = dim_config.get("value_mapping", {})
        filter_value = value_mapping.get(user_value, user_value)

        for table in tables_in_query:
            if table == source_table:
                table_ref = _find_table_alias(sql, table, dataset)
                if filter_value == "__NOT__":
                    exclude_value = value_mapping.get("__exclude__", "")
                    where_conditions.append(f"{table_ref}.{source_column} != '{exclude_value}'")
                    rules_applied.append(f"{dim_name}: excluding {source_column}='{exclude_value}'")
                else:
                    where_conditions.append(f"{table_ref}.{source_column} = '{filter_value}'")
                    rules_applied.append(f"{dim_name}: {source_column}='{filter_value}'")

            elif table in foreign_keys:
                for fk_col, fk_ref in foreign_keys[table].items():
                    ref_table = fk_ref.split(".")[0]
                    ref_col = fk_ref.split(".")[1]

                    if ref_table == source_table:
                        if source_table not in tables_in_query:
                            joins_to_add.append(
                                f"JOIN `{dataset}.{source_table}` ON `{dataset}.{table}`.{fk_col} = `{dataset}.{source_table}`.{ref_col}"
                            )
                        source_ref = _find_table_alias(sql, source_table, dataset)
                        if filter_value == "__NOT__":
                            exclude_value = value_mapping.get("__exclude__", "")
                            where_conditions.append(f"{source_ref}.{source_column} != '{exclude_value}'")
                            rules_applied.append(f"{dim_name}: excluding {source_column}='{exclude_value}' (via {table}.{fk_col})")
                        else:
                            where_conditions.append(f"{source_ref}.{source_column} = '{filter_value}'")
                            rules_applied.append(f"{dim_name}: {source_column}='{filter_value}' (via {table}.{fk_col})")
                        break

    where_conditions = list(set(where_conditions))
    joins_to_add = list(set(joins_to_add))

    if not joins_to_add and not where_conditions:
        return {"sql": sql, "rules_applied": [], "access_restricted": False}

    modified_sql = sql

    if joins_to_add:
        from_match = re.search(r'(FROM\s+`[^`]+`)', modified_sql, re.IGNORECASE)
        if from_match:
            join_str = " " + " ".join(joins_to_add)
            modified_sql = modified_sql[:from_match.end()] + join_str + modified_sql[from_match.end():]

    if where_conditions:
        combined = " AND ".join(where_conditions)

        if re.search(r'\bWHERE\b', modified_sql, re.IGNORECASE):
            modified_sql = re.sub(r'\bWHERE\b', f'WHERE ({combined}) AND ', modified_sql, count=1, flags=re.IGNORECASE)
        else:
            # Find insertion point, stripping any trailing whitespace before GROUP BY/ORDER BY/LIMIT
            for pattern in [r'\s*\bGROUP\s+BY\b', r'\s*\bORDER\s+BY\b', r'\s*\bLIMIT\b']:
                match = re.search(pattern, modified_sql, re.IGNORECASE)
                if match:
                    # Insert WHERE clause, preserving the matched keyword
                    keyword_start = match.start()
                    keyword_text = modified_sql[match.start():match.end()].lstrip()
                    modified_sql = modified_sql[:keyword_start] + f"\nWHERE {combined}\n{keyword_text}" + modified_sql[match.end():]
                    break
            else:
                modified_sql = modified_sql.rstrip(';') + f"\nWHERE {combined}"

    return {
        "sql": modified_sql,
        "original_sql": sql,
        "rules_applied": rules_applied,
        "access_restricted": True
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

    # Read existing content
    existing = ""
    if os.path.exists(CONTEXT_FILE):
        with open(CONTEXT_FILE, "r") as f:
            existing = f.read()

    # Find or create section
    section_header = f"## {section.replace('_', ' ').title()}"
    if section_header not in existing:
        existing += f"\n\n{section_header}\n"

    # Append to section
    lines = existing.split('\n')
    new_lines = []
    in_section = False
    content_added = False

    for i, line in enumerate(lines):
        new_lines.append(line)
        if line.strip() == section_header:
            in_section = True
        elif in_section and line.startswith("## "):
            # Start of new section, insert content before it
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
    Access control is automatically enforced based on the current user identity.

    Args:
        sql: The SQL query to execute
    """
    global _current_identity
    config = _get_config()

    if not config.get("dataset"):
        return "Error: No dataset configured. Run 'text2sql init' first."

    # Apply access control
    result = _apply_access_control(sql, _current_identity, config)

    if "error" in result:
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

    if result.get("access_restricted"):
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
def manage_access_rules(
    operation: str,
    dimension_name: str = None,
    user_attribute: str = None,
    source_table: str = None,
    source_column: str = None,
    value_mapping: str = None
) -> str:
    """
    Manage access control dimensions.

    Args:
        operation: One of 'list', 'get', 'set', 'remove'
        dimension_name: Name of the dimension (required for get/set/remove)
        user_attribute: Column in users table that determines access (for set)
        source_table: Table containing the data to filter (for set)
        source_column: Column to filter on (for set)
        value_mapping: JSON string mapping user values to DB values (for set)
    """
    config = _get_config()
    access_dimensions = config.get("access_dimensions", {})

    if operation == "list":
        if not access_dimensions:
            return "No access dimensions configured."
        output = ["Configured access dimensions:"]
        for name, dim in access_dimensions.items():
            output.append(f"\n{name}:")
            output.append(f"  User attribute: {dim.get('user_attribute')}")
            output.append(f"  Source: {dim.get('source_table')}.{dim.get('source_column')}")
            if dim.get('value_mapping'):
                output.append(f"  Value mapping: {json.dumps(dim.get('value_mapping'))}")
        return "\n".join(output)

    elif operation == "get":
        if not dimension_name:
            return "Error: dimension_name required for 'get' operation"
        if dimension_name not in access_dimensions:
            return f"Dimension '{dimension_name}' not found."
        dim = access_dimensions[dimension_name]
        return json.dumps(dim, indent=2)

    elif operation == "set":
        if not all([dimension_name, user_attribute, source_table, source_column]):
            return "Error: dimension_name, user_attribute, source_table, source_column all required for 'set'"

        mapping = {}
        if value_mapping:
            try:
                mapping = json.loads(value_mapping)
            except json.JSONDecodeError:
                return "Error: value_mapping must be valid JSON"

        access_dimensions[dimension_name] = {
            "user_attribute": user_attribute,
            "source_table": source_table,
            "source_column": source_column,
            "value_mapping": mapping
        }
        config["access_dimensions"] = access_dimensions
        _save_config(config)
        return f"Access dimension '{dimension_name}' configured successfully."

    elif operation == "remove":
        if not dimension_name:
            return "Error: dimension_name required for 'remove' operation"
        if dimension_name not in access_dimensions:
            return f"Dimension '{dimension_name}' not found."
        del access_dimensions[dimension_name]
        config["access_dimensions"] = access_dimensions
        _save_config(config)
        return f"Access dimension '{dimension_name}' removed."

    else:
        return f"Unknown operation '{operation}'. Use: list, get, set, remove"


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

            # Find user by any field value
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
        # Show current config
        output = ["Current configuration:"]
        output.append(f"  Dataset: {config.get('dataset', 'NOT SET')}")
        output.append(f"  Users table: {config.get('users_table', 'NOT SET')}")
        return "\n".join(output)

    if dataset:
        # Validate dataset exists
        try:
            schema = _load_schema(dataset)
            if "error" in schema:
                return f"Error: Could not access dataset '{dataset}': {schema['error']}"
            config["dataset"] = dataset
            _save_config(config)

            # Update context file with schema
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
    main()
