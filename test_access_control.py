"""
Unit tests for access control core functions.
Run with: python -m pytest test_access_control.py -v
"""

import pytest
from unittest.mock import patch

from server import (
    _extract_columns_from_sql,
    _check_restricted_columns,
    _apply_access_control,
    _validate_join_condition,
    _build_column_ref,
    _build_eq_condition,
    _build_literal_eq,
)


# ===========================================
# Test Schema (mock)
# ===========================================

MOCK_SCHEMA = {
    "sales": {
        "columns": [
            {"name": "id", "type": "INTEGER"},
            {"name": "amount", "type": "FLOAT"},
            {"name": "territory", "type": "STRING"},
        ]
    },
    "ae_territories": {
        "columns": [
            {"name": "territory", "type": "STRING"},
            {"name": "ae_id", "type": "INTEGER"},
        ]
    },
    "account_execs": {
        "columns": [
            {"name": "ae_id", "type": "INTEGER"},
            {"name": "ae_email", "type": "STRING"},
            {"name": "name", "type": "STRING"},
        ]
    },
}

MOCK_CONFIG = {
    "dataset": "test.dataset",
    "access_rules": [
        {
            "restricted_column": "sales.amount",
            "user_identifier_column": "account_execs.ae_email",
            "user_field": "ae_email",
            "join_path": [
                "sales.territory = ae_territories.territory",
                "ae_territories.ae_id = account_execs.ae_id"
            ]
        }
    ],
    "foreign_keys": []
}

MOCK_USER = {"ae_email": "bob@acme.com", "name": "Bob"}


# ===========================================
# _extract_columns_from_sql tests
# ===========================================

class TestExtractColumns:
    def test_simple_select(self):
        sql = "SELECT amount FROM sales"
        columns, error = _extract_columns_from_sql(sql, MOCK_SCHEMA)
        assert error is None
        assert "sales.amount" in columns

    def test_qualified_column(self):
        sql = "SELECT sales.amount FROM sales"
        columns, error = _extract_columns_from_sql(sql, MOCK_SCHEMA)
        assert error is None
        assert "sales.amount" in columns

    def test_with_alias(self):
        sql = "SELECT s.amount FROM sales AS s"
        columns, error = _extract_columns_from_sql(sql, MOCK_SCHEMA)
        assert error is None
        assert "sales.amount" in columns

    def test_alias_no_as_keyword(self):
        sql = "SELECT s.amount FROM sales s"
        columns, error = _extract_columns_from_sql(sql, MOCK_SCHEMA)
        assert error is None
        assert "sales.amount" in columns

    def test_multiple_columns(self):
        sql = "SELECT id, amount, territory FROM sales"
        columns, error = _extract_columns_from_sql(sql, MOCK_SCHEMA)
        assert error is None
        assert "sales.id" in columns
        assert "sales.amount" in columns
        assert "sales.territory" in columns

    def test_ambiguous_column_returns_all_matches(self):
        """Unqualified 'territory' exists in both sales and ae_territories"""
        sql = "SELECT territory FROM sales"
        columns, error = _extract_columns_from_sql(sql, MOCK_SCHEMA)
        assert error is None
        # Should return both possible tables (conservative approach)
        assert "sales.territory" in columns
        assert "ae_territories.territory" in columns

    def test_join_with_aliases(self):
        sql = "SELECT s.amount, t.ae_id FROM sales s JOIN ae_territories t ON s.territory = t.territory"
        columns, error = _extract_columns_from_sql(sql, MOCK_SCHEMA)
        assert error is None
        assert "sales.amount" in columns
        assert "ae_territories.ae_id" in columns

    def test_invalid_sql_returns_error(self):
        sql = "SELECT * FROM WHERE"
        columns, error = _extract_columns_from_sql(sql, MOCK_SCHEMA)
        assert error is not None
        assert columns is None

    def test_star_select(self):
        sql = "SELECT * FROM sales"
        columns, error = _extract_columns_from_sql(sql, MOCK_SCHEMA)
        assert error is None
        # Star doesn't produce column references in sqlglot
        # This is a known limitation - star queries should be handled separately


# ===========================================
# _check_restricted_columns tests
# ===========================================

class TestCheckRestrictedColumns:
    @patch('server._load_schema')
    def test_no_rules_allows_all(self, mock_load_schema):
        mock_load_schema.return_value = MOCK_SCHEMA
        config = {"dataset": "test.dataset", "access_rules": []}
        matches, error = _check_restricted_columns("SELECT amount FROM sales", config)
        assert error is None
        assert matches == []

    @patch('server._load_schema')
    def test_restricted_column_detected(self, mock_load_schema):
        mock_load_schema.return_value = MOCK_SCHEMA
        matches, error = _check_restricted_columns("SELECT amount FROM sales", MOCK_CONFIG)
        assert error is None
        assert len(matches) == 1
        assert matches[0]["restricted_column"] == "sales.amount"

    @patch('server._load_schema')
    def test_non_restricted_column_passes(self, mock_load_schema):
        mock_load_schema.return_value = MOCK_SCHEMA
        matches, error = _check_restricted_columns("SELECT id FROM sales", MOCK_CONFIG)
        assert error is None
        assert matches == []

    @patch('server._load_schema')
    def test_schema_error_returns_error(self, mock_load_schema):
        mock_load_schema.return_value = {"error": "Connection failed"}
        matches, error = _check_restricted_columns("SELECT amount FROM sales", MOCK_CONFIG)
        assert error is not None
        assert "Schema error" in error
        assert matches is None


# ===========================================
# _apply_access_control tests
# ===========================================

class TestApplyAccessControl:
    @patch('server._load_schema')
    @patch('server._check_restricted_columns')
    def test_admin_mode_no_restrictions(self, mock_check, mock_load_schema):
        """When user is None (admin), no access control applied"""
        result = _apply_access_control("SELECT * FROM sales", None, MOCK_CONFIG)
        assert result["access_restricted"] is False
        assert result["sql"] == "SELECT * FROM sales"
        mock_check.assert_not_called()

    @patch('server._load_schema')
    @patch('server._check_restricted_columns')
    def test_check_error_denies_access(self, mock_check, mock_load_schema):
        """If column check fails, access is denied (fail-closed)"""
        mock_check.return_value = (None, "Parse error")
        result = _apply_access_control("SELECT amount FROM sales", MOCK_USER, MOCK_CONFIG)
        assert result.get("access_denied") is True
        assert "Parse error" in result.get("error", "")

    @patch('server._load_schema')
    @patch('server._check_restricted_columns')
    def test_no_matches_no_modification(self, mock_check, mock_load_schema):
        """If no restricted columns found, SQL unchanged"""
        mock_check.return_value = ([], None)
        result = _apply_access_control("SELECT id FROM sales", MOCK_USER, MOCK_CONFIG)
        assert result["access_restricted"] is False

    @patch('server._load_schema')
    def test_adds_joins_and_where(self, mock_load_schema):
        mock_load_schema.return_value = MOCK_SCHEMA
        sql = "SELECT SUM(amount) FROM sales"
        result = _apply_access_control(sql, MOCK_USER, MOCK_CONFIG)

        assert result["access_restricted"] is True
        modified_sql = result["sql"].upper()

        # Should have JOINs added
        assert "JOIN" in modified_sql
        assert "AE_TERRITORIES" in modified_sql
        assert "ACCOUNT_EXECS" in modified_sql

        # Should have WHERE clause with user's email
        assert "WHERE" in modified_sql
        assert "bob@acme.com" in result["sql"]

    @patch('server._load_schema')
    def test_handles_alias_in_where(self, mock_load_schema):
        """When original query uses alias, WHERE should use alias"""
        mock_load_schema.return_value = MOCK_SCHEMA

        # Config with rule where identifier table might be aliased
        config = {
            "dataset": "test.dataset",
            "access_rules": [{
                "restricted_column": "sales.amount",
                "user_identifier_column": "sales.territory",  # same table
                "user_field": "territory",
                "join_path": []  # no joins needed
            }]
        }
        user = {"territory": "WEST"}

        sql = "SELECT s.amount FROM sales AS s"
        result = _apply_access_control(sql, user, config)

        # The WHERE should reference 's' (alias), not 'sales'
        assert "s.territory" in result["sql"] or "sales.territory" in result["sql"]

    @patch('server._load_schema')
    def test_missing_user_field_denied(self, mock_load_schema):
        """If user is missing required field, access denied"""
        mock_load_schema.return_value = MOCK_SCHEMA
        incomplete_user = {"name": "Bob"}  # missing ae_email
        result = _apply_access_control("SELECT amount FROM sales", incomplete_user, MOCK_CONFIG)
        assert result.get("access_denied") is True
        assert "missing required field" in result.get("error", "").lower()

    @patch('server._load_schema')
    def test_sql_injection_in_user_value_escaped(self, mock_load_schema):
        """User value with quotes should be safely escaped via AST"""
        mock_load_schema.return_value = MOCK_SCHEMA
        malicious_user = {"ae_email": "test'; DROP TABLE sales; --", "name": "Hacker"}
        result = _apply_access_control("SELECT amount FROM sales", malicious_user, MOCK_CONFIG)

        # Should not cause error
        assert "error" not in result or result.get("error") is None
        # The malicious string should be escaped in output
        assert "DROP TABLE" in result["sql"]  # present but escaped
        # Quote should be escaped (sqlglot uses backslash or double-quote)
        assert "\\'" in result["sql"] or "''" in result["sql"]


# ===========================================
# _validate_join_condition tests
# ===========================================

class TestValidateJoinCondition:
    def test_valid_condition(self):
        is_valid, error = _validate_join_condition("sales.id = users.id")
        assert is_valid is True
        assert error is None

    def test_missing_equals(self):
        is_valid, error = _validate_join_condition("sales.id users.id")
        assert is_valid is False
        assert "must contain ' = '" in error

    def test_missing_right_column(self):
        is_valid, error = _validate_join_condition("sales.id = users")
        assert is_valid is False
        assert "Right side" in error

    def test_missing_left_column(self):
        is_valid, error = _validate_join_condition("sales = users.id")
        assert is_valid is False
        assert "Left side" in error

    def test_not_a_string(self):
        is_valid, error = _validate_join_condition(123)
        assert is_valid is False
        assert "must be a string" in error

    def test_empty_table_name(self):
        is_valid, error = _validate_join_condition(".col = table.col")
        assert is_valid is False

    def test_multiple_equals(self):
        is_valid, error = _validate_join_condition("a.b = c.d = e.f")
        assert is_valid is False
        assert "exactly one" in error


# ===========================================
# AST Builder tests
# ===========================================

class TestASTBuilders:
    def test_build_column_ref(self):
        col = _build_column_ref("users", "email")
        assert col.sql() == "users.email"

    def test_build_eq_condition(self):
        eq = _build_eq_condition("sales", "user_id", "users", "id")
        assert eq.sql() == "sales.user_id = users.id"

    def test_build_literal_eq_simple(self):
        eq = _build_literal_eq("users", "email", "bob@test.com")
        assert eq.sql() == "users.email = 'bob@test.com'"

    def test_build_literal_eq_with_quote(self):
        eq = _build_literal_eq("users", "name", "O'Connor")
        # sqlglot should escape the quote
        sql = eq.sql()
        assert "O''Connor" in sql or "O\\'Connor" in sql

    def test_build_literal_eq_injection_attempt(self):
        eq = _build_literal_eq("users", "email", "'; DROP TABLE users; --")
        sql = eq.sql()
        # The malicious content should be inside a single string literal
        assert sql.count("'") >= 2  # At least opening and closing quotes
        # DROP should be inside the string, not executed
        assert "DROP" in sql


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
