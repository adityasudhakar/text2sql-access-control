"""
Text-to-SQL MCP Server with Column-Level Access Control

An MCP server that provides text-to-SQL capabilities with column-level access control.
Uses sqlglot for SQL parsing and deterministic join-based enforcement.
"""

import os
import json
from typing import Any
from collections import defaultdict
from google.cloud import bigquery
from anthropic import Anthropic
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import build_scope

# Load environment
load_dotenv()

# Initialize FastMCP server
mcp = FastMCP("text2sql")

# Get directory where this script lives (for resolving relative paths)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Configuration paths (anchored to script location, not cwd)
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
CONTEXT_FILE = os.path.join(SCRIPT_DIR, "context.md")
SERVICE_ACCOUNT_FILE = os.path.join(SCRIPT_DIR, "service_account.json")

# Global state
_bq_client = None
_config = None
_current_identity = None
_anthropic_client = None
_schema_cache = None


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
    global _schema_cache
    if _schema_cache is not None:
        return _schema_cache

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
        _schema_cache = schema
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


# ============================================
# FK Detection and Path Finding
# ============================================

def _validate_join_condition(join_cond: str) -> tuple:
    """
    Validate that a join condition matches expected format: "table.column = table.column"
    Returns (is_valid, error_message)
    """
    if not isinstance(join_cond, str):
        return (False, f"Join condition must be a string, got: {type(join_cond).__name__}")

    if " = " not in join_cond:
        return (False, f"Join condition must contain ' = ', got: '{join_cond}'")

    parts = join_cond.split(" = ")
    if len(parts) != 2:
        return (False, f"Join condition must have exactly one ' = ', got: '{join_cond}'")

    left, right = parts[0].strip(), parts[1].strip()

    # Validate left side: table.column
    if "." not in left:
        return (False, f"Left side must be 'table.column', got: '{left}'")
    left_parts = left.split(".")
    if len(left_parts) != 2 or not left_parts[0] or not left_parts[1]:
        return (False, f"Left side must be exactly 'table.column', got: '{left}'")

    # Validate right side: table.column
    if "." not in right:
        return (False, f"Right side must be 'table.column', got: '{right}'")
    right_parts = right.split(".")
    if len(right_parts) != 2 or not right_parts[0] or not right_parts[1]:
        return (False, f"Right side must be exactly 'table.column', got: '{right}'")

    return (True, None)


def _detect_foreign_keys(schema: dict) -> list:
    """
    Detect likely foreign key relationships by naming convention.
    Returns list of {"from_table": t1, "from_column": c1, "to_table": t2, "to_column": c2}
    """
    fks = []

    # Build column lookup: column_name -> list of tables that have it
    column_tables = defaultdict(list)
    for table_name, table_info in schema.items():
        for col in table_info["columns"]:
            column_tables[col["name"].lower()].append(table_name)

    # Find columns that appear in multiple tables (likely FKs)
    for col_name, tables in column_tables.items():
        if len(tables) > 1:
            # Assume the column links these tables
            for i, t1 in enumerate(tables):
                for t2 in tables[i+1:]:
                    fks.append({
                        "from_table": t1,
                        "from_column": col_name,
                        "to_table": t2,
                        "to_column": col_name
                    })

    # Also look for patterns like "user_id" -> "users.id"
    for table_name, table_info in schema.items():
        for col in table_info["columns"]:
            col_lower = col["name"].lower()
            # Pattern: {table}_id or {table}id
            for other_table in schema.keys():
                other_lower = other_table.lower()
                # Check if column is like "users_id" or "user_id" for table "users"
                if col_lower == f"{other_lower}_id" or col_lower == f"{other_lower[:-1]}_id" if other_lower.endswith('s') else False:
                    # Check if other table has "id" column
                    other_cols = [c["name"].lower() for c in schema[other_table]["columns"]]
                    if "id" in other_cols:
                        fks.append({
                            "from_table": table_name,
                            "from_column": col["name"],
                            "to_table": other_table,
                            "to_column": "id"
                        })

    return fks


def _find_join_path(schema: dict, fks: list, from_table: str, to_table: str) -> list:
    """
    Find shortest path of joins from from_table to to_table using BFS.
    Returns list of join conditions like ["table1.col = table2.col", ...]
    """
    if from_table == to_table:
        return []

    # Build adjacency list
    graph = defaultdict(list)
    for fk in fks:
        graph[fk["from_table"]].append({
            "table": fk["to_table"],
            "join": f"{fk['from_table']}.{fk['from_column']} = {fk['to_table']}.{fk['to_column']}"
        })
        graph[fk["to_table"]].append({
            "table": fk["from_table"],
            "join": f"{fk['to_table']}.{fk['to_column']} = {fk['from_table']}.{fk['from_column']}"
        })

    # BFS
    from collections import deque
    queue = deque([(from_table, [])])
    visited = {from_table}

    while queue:
        current, path = queue.popleft()

        for neighbor in graph[current]:
            if neighbor["table"] == to_table:
                return path + [neighbor["join"]]

            if neighbor["table"] not in visited:
                visited.add(neighbor["table"])
                queue.append((neighbor["table"], path + [neighbor["join"]]))

    return None  # No path found


# ============================================
# SQL Parsing and Column Detection
# ============================================

def _extract_columns_from_sql(sql: str, schema: dict) -> tuple:
    """
    Parse SQL and extract all column references as table.column.
    Resolves aliases to actual table names.

    Returns (columns, error) tuple. If error is not None, parsing failed.
    """
    try:
        parsed = sqlglot.parse_one(sql, dialect='bigquery')
    except Exception as e:
        return (None, f"SQL parse error: {e}")

    # Build alias -> table mapping
    alias_map = {}
    try:
        root = build_scope(parsed)
        if root:
            for alias, source in root.sources.items():
                if isinstance(source, exp.Table):
                    alias_map[alias] = source.name
    except Exception as e:
        # Non-fatal - we can still try to extract columns
        pass

    # Also check FROM clause directly for simple cases
    for table in parsed.find_all(exp.Table):
        if table.alias:
            alias_map[table.alias] = table.name
        else:
            alias_map[table.name] = table.name

    # Extract all column references
    columns = []
    for col in parsed.find_all(exp.Column):
        col_name = col.name
        table_ref = col.table if col.table else None

        # Resolve alias to actual table name
        if table_ref and table_ref in alias_map:
            actual_table = alias_map[table_ref]
            columns.append(f"{actual_table}.{col_name}")
        elif table_ref:
            columns.append(f"{table_ref}.{col_name}")
        else:
            # No table specified - find ALL tables that have this column
            # This is conservative: if column is ambiguous and ANY match is restricted,
            # the rule will be applied (fail-closed behavior)
            matching_tables = []
            for table_name, table_info in schema.items():
                if col_name.lower() in [c["name"].lower() for c in table_info["columns"]]:
                    matching_tables.append(table_name)

            if matching_tables:
                for table_name in matching_tables:
                    columns.append(f"{table_name}.{col_name}")
            # If no matching table found, column might be computed/literal - skip it

    return (columns, None)


def _check_restricted_columns(sql: str, config: dict) -> tuple:
    """
    Check if SQL contains any restricted columns using proper parsing.
    Returns (matches, error) tuple. If error is not None, check failed and query should be denied.
    """
    schema = _load_schema(config.get("dataset", ""))
    if "error" in schema:
        return (None, f"Schema error: {schema['error']}")

    restrictions = config.get("access_rules", [])
    if not restrictions:
        return ([], None)  # No rules configured - allow

    # Extract columns from SQL
    sql_columns, parse_error = _extract_columns_from_sql(sql, schema)
    if parse_error:
        return (None, parse_error)

    sql_columns_lower = [c.lower() for c in sql_columns]

    matches = []
    for rule in restrictions:
        restricted_col = rule["restricted_column"].lower()
        if restricted_col in sql_columns_lower:
            matches.append(rule)

    return (matches, None)


# ============================================
# Deterministic SQL Rewriting
# ============================================

def _build_column_ref(table: str, column: str) -> exp.Column:
    """Build an AST-safe column reference."""
    return exp.Column(this=exp.to_identifier(column), table=exp.to_identifier(table))


def _build_eq_condition(left_table: str, left_col: str, right_table: str, right_col: str) -> exp.EQ:
    """Build an AST-safe equality condition between two columns."""
    return exp.EQ(
        this=_build_column_ref(left_table, left_col),
        expression=_build_column_ref(right_table, right_col)
    )


def _build_literal_eq(table: str, column: str, value: str) -> exp.EQ:
    """Build an AST-safe equality condition: table.column = 'literal_value'"""
    return exp.EQ(
        this=_build_column_ref(table, column),
        expression=exp.Literal.string(value)
    )


def _apply_access_control(sql: str, user: dict, config: dict) -> dict:
    """
    Apply column-level access control to SQL query deterministically.
    1. Parse SQL to find restricted columns
    2. Add necessary JOINs and WHERE clauses

    FAIL-CLOSED: If parsing or schema lookup fails, query is denied.
    """
    if user is None:
        # Admin mode - no restrictions
        return {"sql": sql, "rules_applied": [], "access_restricted": False}

    # Check for restricted columns (fail-closed on errors)
    matches, check_error = _check_restricted_columns(sql, config)

    if check_error:
        # FAIL-CLOSED: deny query if we can't verify it's safe
        return {
            "sql": sql,
            "error": f"Access control check failed: {check_error}",
            "access_denied": True
        }

    if not matches:
        return {"sql": sql, "rules_applied": [], "access_restricted": False}

    schema = _load_schema(config.get("dataset", ""))
    fks = config.get("foreign_keys", [])
    if not fks:
        fks = _detect_foreign_keys(schema)

    # Parse the SQL
    try:
        parsed = sqlglot.parse_one(sql, dialect='bigquery')
    except Exception as e:
        return {"sql": sql, "error": f"Could not parse SQL: {e}", "access_denied": True}

    # Build table name -> alias mapping (and track which tables exist)
    # If table has alias, we must use alias in references; otherwise use table name
    table_to_ref = {}  # table_name -> reference to use (alias if exists, else table_name)
    for table in parsed.find_all(exp.Table):
        if table.alias:
            table_to_ref[table.name] = table.alias
        else:
            table_to_ref[table.name] = table.name

    rules_applied = []

    for rule in matches:
        restricted_col = rule["restricted_column"]
        user_identifier_col = rule["user_identifier_column"]  # e.g., "account_execs.ae_email"

        # Get user's value for the identifier
        user_field = rule.get("user_field", user_identifier_col.split(".")[-1])
        user_value = user.get(user_field)

        if user_value is None:
            return {
                "sql": sql,
                "error": f"User missing required field '{user_field}' for access control",
                "access_denied": True
            }

        # Get table names
        restricted_table = restricted_col.split(".")[0]
        identifier_table = user_identifier_col.split(".")[0]
        identifier_column = user_identifier_col.split(".")[1]

        # Find join path from restricted table to identifier table
        join_path = rule.get("join_path")
        if not join_path:
            join_path = _find_join_path(schema, fks, restricted_table, identifier_table)

        if join_path is None:
            return {
                "sql": sql,
                "error": f"No join path found from '{restricted_table}' to '{identifier_table}'",
                "access_denied": True
            }

        # Add JOINs to the query
        for join_cond in join_path:
            # Parse the join condition to get the tables involved
            # Format: "table1.col1 = table2.col2"
            parts = join_cond.split(" = ")
            left_table = parts[0].split(".")[0]
            left_col = parts[0].split(".")[1]
            right_table = parts[1].split(".")[0]
            right_col = parts[1].split(".")[1]

            # Determine which table we need to add
            existing_tables = set(table_to_ref.keys())

            if right_table not in existing_tables:
                join_table = right_table
                # Use alias/ref for existing table, base name for new table
                left_ref = table_to_ref.get(left_table, left_table)
                join_on_ast = _build_eq_condition(left_ref, left_col, join_table, right_col)
            elif left_table not in existing_tables:
                join_table = left_table
                right_ref = table_to_ref.get(right_table, right_table)
                join_on_ast = _build_eq_condition(join_table, left_col, right_ref, right_col)
            else:
                continue  # Both tables already in query

            # Add JOIN using Select.join() method with AST expression
            # Fully qualify the table name with dataset for BigQuery compatibility
            dataset = config.get("dataset", "")
            if dataset:
                full_table_name = f"{dataset}.{join_table}"
            else:
                full_table_name = join_table

            parsed = parsed.join(
                exp.to_table(full_table_name),
                on=join_on_ast,
                join_type="INNER"
            )
            # Track newly added table (no alias, so ref = table name)
            table_to_ref[join_table] = join_table

        # Add WHERE condition using AST-safe construction (no string interpolation)
        # Use the correct reference (alias or table name) for the identifier table
        identifier_ref = table_to_ref.get(identifier_table, identifier_table)
        where_ast = _build_literal_eq(identifier_ref, identifier_column, str(user_value))
        parsed = parsed.where(where_ast, append=True)

        rules_applied.append(f"{restricted_col} filtered by {user_identifier_col}='{user_value}'")

    return {
        "sql": parsed.sql(dialect='bigquery'),
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
    If the query touches restricted columns, JOINs and WHERE clauses are added automatically.

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
        output.append(f"Current user: {json.dumps(_current_identity)}")
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
def list_schema_columns() -> str:
    """
    List all tables and columns in the configured dataset.
    Use this to help admin select columns for access control.
    """
    config = _get_config()
    dataset = config.get("dataset")

    if not dataset:
        return "Error: No dataset configured."

    schema = _load_schema(dataset)
    if "error" in schema:
        return f"Error loading schema: {schema['error']}"

    output = ["Available tables and columns:"]
    for table_name, table_info in schema.items():
        output.append(f"\n{table_name}:")
        for col in table_info["columns"]:
            output.append(f"  - {col['name']} ({col['type']})")

    return "\n".join(output)


@mcp.tool()
def detect_foreign_keys() -> str:
    """
    Detect likely foreign key relationships in the schema by naming convention.
    Returns detected FK relationships that can be used for join paths.
    """
    config = _get_config()
    dataset = config.get("dataset")

    if not dataset:
        return "Error: No dataset configured."

    schema = _load_schema(dataset)
    if "error" in schema:
        return f"Error loading schema: {schema['error']}"

    fks = _detect_foreign_keys(schema)

    if not fks:
        return "No foreign key relationships detected by naming convention."

    output = ["Detected foreign key relationships:"]
    for fk in fks:
        output.append(f"  {fk['from_table']}.{fk['from_column']} -> {fk['to_table']}.{fk['to_column']}")

    output.append("\nThese will be used automatically for join paths.")
    output.append("Use set_foreign_key to add or override relationships.")

    return "\n".join(output)


@mcp.tool()
def set_foreign_key(from_table: str, from_column: str, to_table: str, to_column: str) -> str:
    """
    Manually set a foreign key relationship for join path resolution.

    Args:
        from_table: Source table name
        from_column: Source column name
        to_table: Target table name
        to_column: Target column name
    """
    config = _get_config()
    fks = config.get("foreign_keys", [])

    fk = {
        "from_table": from_table,
        "from_column": from_column,
        "to_table": to_table,
        "to_column": to_column
    }

    # Check if this FK already exists
    for existing in fks:
        if (existing["from_table"] == from_table and
            existing["from_column"] == from_column and
            existing["to_table"] == to_table):
            existing["to_column"] = to_column
            config["foreign_keys"] = fks
            _save_config(config)
            return f"Updated FK: {from_table}.{from_column} -> {to_table}.{to_column}"

    fks.append(fk)
    config["foreign_keys"] = fks
    _save_config(config)

    return f"Added FK: {from_table}.{from_column} -> {to_table}.{to_column}"


@mcp.tool()
def set_access_rule(
    restricted_column: str,
    user_identifier_column: str,
    user_field: str = None,
    join_path: str = None
) -> str:
    """
    Set an access control rule: restrict a column based on user identity.

    Args:
        restricted_column: Column to restrict, e.g., "sales.amount"
        user_identifier_column: Column that identifies allowed users, e.g., "account_execs.ae_email"
        user_field: Field in user record to match (defaults to last part of user_identifier_column)
        join_path: Optional JSON array of join conditions (auto-detected if not provided)
    """
    config = _get_config()
    schema = _load_schema(config.get("dataset", ""))

    if "error" in schema:
        return f"Error loading schema: {schema['error']}"

    # Validate restricted_column format
    if not restricted_column or "." not in restricted_column:
        return f"Error: restricted_column must be in 'table.column' format, got: '{restricted_column}'"
    r_parts = restricted_column.split(".")
    if len(r_parts) != 2:
        return f"Error: restricted_column must be exactly 'table.column' format, got: '{restricted_column}'"
    r_table, r_col = r_parts

    # Validate user_identifier_column format
    if not user_identifier_column or "." not in user_identifier_column:
        return f"Error: user_identifier_column must be in 'table.column' format, got: '{user_identifier_column}'"
    u_parts = user_identifier_column.split(".")
    if len(u_parts) != 2:
        return f"Error: user_identifier_column must be exactly 'table.column' format, got: '{user_identifier_column}'"
    u_table, u_col = u_parts

    # Validate restricted column exists in schema
    if r_table not in schema:
        return f"Error: Table '{r_table}' not found in schema"
    if r_col not in [c["name"] for c in schema[r_table]["columns"]]:
        return f"Error: Column '{r_col}' not found in table '{r_table}'"

    # Validate user identifier column exists in schema
    if u_table not in schema:
        return f"Error: Table '{u_table}' not found in schema"
    if u_col not in [c["name"] for c in schema[u_table]["columns"]]:
        return f"Error: Column '{u_col}' not found in table '{u_table}'"

    # Parse and validate join path if provided
    parsed_join_path = None
    if join_path:
        try:
            parsed_join_path = json.loads(join_path)
        except:
            return "Error: join_path must be a valid JSON array"

        # Validate it's a list
        if not isinstance(parsed_join_path, list):
            return "Error: join_path must be a JSON array of join conditions"

        # Validate each join condition format
        for i, join_cond in enumerate(parsed_join_path):
            is_valid, error = _validate_join_condition(join_cond)
            if not is_valid:
                return f"Error in join_path[{i}]: {error}"

    # Auto-detect join path if not provided
    if not parsed_join_path:
        fks = config.get("foreign_keys", [])
        if not fks:
            fks = _detect_foreign_keys(schema)
        parsed_join_path = _find_join_path(schema, fks, r_table, u_table)

        if parsed_join_path is None:
            return f"Error: No join path found from '{r_table}' to '{u_table}'. Use set_foreign_key to define relationships."

    rule = {
        "restricted_column": restricted_column,
        "user_identifier_column": user_identifier_column,
        "user_field": user_field or u_col,
        "join_path": parsed_join_path
    }

    rules = config.get("access_rules", [])

    # Replace existing rule for same column
    rules = [r for r in rules if r["restricted_column"] != restricted_column]
    rules.append(rule)

    config["access_rules"] = rules
    _save_config(config)

    output = [f"Access rule set for '{restricted_column}':"]
    output.append(f"  Controlled by: {user_identifier_column}")
    output.append(f"  User field: {rule['user_field']}")
    output.append(f"  Join path: {' -> '.join(parsed_join_path) if parsed_join_path else 'direct'}")

    return "\n".join(output)


@mcp.tool()
def list_access_rules() -> str:
    """List all configured access control rules."""
    config = _get_config()
    rules = config.get("access_rules", [])

    if not rules:
        return "No access rules configured."

    output = ["Configured access rules:"]
    for rule in rules:
        output.append(f"\n{rule['restricted_column']}:")
        output.append(f"  Controlled by: {rule['user_identifier_column']}")
        output.append(f"  User field: {rule['user_field']}")
        output.append(f"  Join path: {' -> '.join(rule.get('join_path', []))}")

    return "\n".join(output)


@mcp.tool()
def remove_access_rule(restricted_column: str) -> str:
    """
    Remove an access control rule.

    Args:
        restricted_column: The column to remove restriction from
    """
    config = _get_config()
    rules = config.get("access_rules", [])

    new_rules = [r for r in rules if r["restricted_column"] != restricted_column]

    if len(new_rules) == len(rules):
        return f"No rule found for '{restricted_column}'."

    config["access_rules"] = new_rules
    _save_config(config)

    return f"Removed access rule for '{restricted_column}'."


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
    global _schema_cache
    config = _get_config()

    if dataset is None and users_table is None:
        output = ["Current configuration:"]
        output.append(f"  Dataset: {config.get('dataset', 'NOT SET')}")
        output.append(f"  Users table: {config.get('users_table', 'NOT SET')}")
        return "\n".join(output)

    if dataset:
        _schema_cache = None  # Clear cache
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
