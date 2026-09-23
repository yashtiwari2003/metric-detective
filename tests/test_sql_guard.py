"""
Tests for the SQL guard module.
Verifies that destructive SQL is blocked and safe queries pass through.
"""
import pytest
from src.safety.sql_guard import validate_sql


@pytest.fixture
def valid_tables():
    """Tables the guard should accept."""
    return ['fact_orders', 'kpi_daily']


@pytest.fixture
def valid_columns():
    """Columns the guard should accept (dict mapping table -> columns)."""
    return {
        'fact_orders': ['order_id', 'revenue', 'order_date', 'customer_id'],
        'kpi_daily': ['order_date', 'revenue', 'orders'],
    }


# ── Blocked statements ──────────────────────────────────────

def test_drop_table_blocked(valid_tables, valid_columns):
    is_valid, msg = validate_sql("DROP TABLE fact_orders;", valid_tables, valid_columns)
    assert not is_valid, f"DROP should be blocked, got: {msg}"


def test_delete_from_blocked(valid_tables, valid_columns):
    is_valid, msg = validate_sql("DELETE FROM fact_orders WHERE order_id = 1;", valid_tables, valid_columns)
    assert not is_valid, f"DELETE should be blocked, got: {msg}"


def test_update_blocked(valid_tables, valid_columns):
    is_valid, msg = validate_sql("UPDATE fact_orders SET revenue = 0;", valid_tables, valid_columns)
    assert not is_valid, f"UPDATE should be blocked, got: {msg}"


def test_insert_into_blocked(valid_tables, valid_columns):
    is_valid, msg = validate_sql("INSERT INTO fact_orders (order_id) VALUES (1);", valid_tables, valid_columns)
    assert not is_valid, f"INSERT should be blocked, got: {msg}"


# ── Allowed statements ──────────────────────────────────────

def test_valid_select_allowed(valid_tables, valid_columns):
    is_valid, cleaned = validate_sql("SELECT revenue FROM fact_orders LIMIT 10;", valid_tables, valid_columns)
    assert is_valid, f"Valid SELECT should pass, got: {cleaned}"
    assert "SELECT" in cleaned.upper()


def test_select_with_cte_allowed(valid_tables, valid_columns):
    query = "WITH cte AS (SELECT * FROM fact_orders) SELECT * FROM cte LIMIT 5;"
    is_valid, cleaned = validate_sql(query, valid_tables, valid_columns)
    assert is_valid, f"CTE should pass, got: {cleaned}"
    assert "WITH" in cleaned.upper()


# ── LIMIT enforcement ────────────────────────────────────────

def test_limit_added_when_missing(valid_tables, valid_columns):
    is_valid, cleaned = validate_sql("SELECT revenue FROM fact_orders;", valid_tables, valid_columns)
    assert is_valid
    assert "LIMIT" in cleaned.upper()


# ── Table validation ─────────────────────────────────────────

def test_invalid_table_names_caught(valid_tables, valid_columns):
    is_valid, msg = validate_sql("SELECT * FROM secret_table LIMIT 10;", valid_tables, valid_columns)
    assert not is_valid, f"Invalid table should be caught, got: {msg}"


# ── Case-insensitive blocking ────────────────────────────────

def test_case_insensitive_blocking(valid_tables, valid_columns):
    queries = [
        "drop table fact_orders",
        "DROP TABLE fact_orders",
        "Drop Table fact_orders",
        "DeLeTe from fact_orders",
    ]
    for query in queries:
        is_valid, msg = validate_sql(query, valid_tables, valid_columns)
        assert not is_valid, f"Should block '{query}', got: {msg}"
