import os
import re
import logging
from typing import Tuple, List, Dict
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv
import threading
import queue

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / '.env')
logger = logging.getLogger(__name__)

def remove_sql_comments(sql: str) -> str:
    """
    Removes single-line and multi-line comments from an SQL string.
    
    Args:
        sql (str): The raw SQL string.
        
    Returns:
        str: SQL string with comments removed.
    """
    # Remove multi-line comments /* ... */
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    # Remove single-line comments -- ...
    sql = re.sub(r'--.*', '', sql)
    return sql.strip()

def validate_sql(
    sql: str, 
    valid_tables: list[str] | None = None, 
    valid_columns: dict[str, list[str]] | None = None
) -> tuple[bool, str]:
    """
    Validates SQL to ensure it's read-only, checks against valid tables, 
    and enforces a LIMIT clause.
    
    Args:
        sql (str): The SQL query to validate.
        valid_tables (list[str] | None): List of valid table names.
        valid_columns (dict[str, list[str]] | None): Dictionary mapping table names to valid columns.
        
    Returns:
        tuple[bool, str]: (is_valid, error_message_or_cleaned_sql)
    """
    if valid_tables is None:
        valid_tables = [
            'fact_orders', 'kpi_daily', 'olist_orders_dataset', 
            'olist_order_items_dataset', 'olist_order_payments_dataset', 
            'olist_order_reviews_dataset', 'olist_customers_dataset', 
            'olist_sellers_dataset', 'olist_products_dataset'
        ]
    clean_sql = remove_sql_comments(sql)
    
    # Block DDL/DML
    forbidden = [r'\bDROP\b', r'\bDELETE\b', r'\bUPDATE\b', r'\bINSERT\b', r'\bALTER\b', 
                 r'\bCREATE\b', r'\bTRUNCATE\b', r'\bGRANT\b', r'\bREVOKE\b', r'\bEXEC\b', r'\bEXECUTE\b']
    
    for f in forbidden:
        if re.search(f, clean_sql, re.IGNORECASE):
            return False, f"Forbidden keyword detected: {f.strip(r'\\b')}"
            
    # Check it starts with SELECT or WITH
    if not re.match(r'^\s*(SELECT|WITH)\b', clean_sql, re.IGNORECASE):
        return False, "Query must start with SELECT or WITH"
        
    # Enforce LIMIT
    if not re.search(r'\bLIMIT\b', clean_sql, re.IGNORECASE):
        clean_sql += "\nLIMIT 1000"
        
    # Extract CTE names (WITH cte_name AS ..., or , cte_name AS ...)
    cte_matches = re.findall(r'(?:WITH|,)\s+([a-zA-Z0-9_]+)\s+AS\s*\(', clean_sql, re.IGNORECASE)
    valid_tables_lower = [vt.lower() for vt in valid_tables] + [c.lower() for c in cte_matches]

    # Validate table names (simple extraction, best effort)
    # Looking for FROM or JOIN followed by a word
    table_matches = re.findall(r'\b(?:FROM|JOIN)\s+([a-zA-Z0-9_]+)\b', clean_sql, re.IGNORECASE)
    for t in table_matches:
        if t.lower() not in valid_tables_lower:
            return False, f"Invalid table referenced: {t}"
            
    return True, clean_sql

def execute_safe_query(engine, sql: str, timeout_seconds: int = 20) -> pd.DataFrame:
    """
    Executes a read-only query with a timeout.
    
    Args:
        engine: SQLAlchemy engine instance.
        sql (str): The validated SQL query to execute.
        timeout_seconds (int): Execution timeout in seconds.
        
    Returns:
        pd.DataFrame: The query result as a DataFrame.
        
    Raises:
        TimeoutError: If query execution exceeds timeout.
    """
    result_q: queue.Queue = queue.Queue()
    
    def run_query():
        try:
            df = pd.read_sql(sql, engine)
            result_q.put(("SUCCESS", df))
        except Exception as e:
            result_q.put(("ERROR", e))
            
    t = threading.Thread(target=run_query, daemon=True)
    t.start()
    t.join(timeout=timeout_seconds)
    
    if t.is_alive():
        raise TimeoutError(f"Query execution exceeded {timeout_seconds} seconds timeout")
        
    try:
        status, result = result_q.get_nowait()
        if status == "ERROR":
            raise result
        return result
    except queue.Empty:
        raise TimeoutError(f"Query execution exceeded {timeout_seconds} seconds timeout (queue empty)")
