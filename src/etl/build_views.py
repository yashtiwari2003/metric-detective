"""
Module to execute SQL scripts to build views and fact tables for the metric detective.
"""
import logging
import os
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv
import sqlalchemy
from sqlalchemy import create_engine, text

# Set up project root and load .env
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / '.env')

DB_URL = os.environ.get("DB_URL", "duckdb:///data/metric.duckdb")

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s'
)
logger = logging.getLogger(__name__)


def _resolve_db_url(db_url: str, project_root: Path) -> str:
    """Resolve relative DuckDB paths against the project root."""
    if db_url.startswith('duckdb:///'):
        db_path = db_url.replace('duckdb:///', '')
        db_full_path = project_root / db_path
        db_full_path.parent.mkdir(parents=True, exist_ok=True)
        return f"duckdb:///{db_full_path}"
    return db_url


def build_fact_orders(engine: sqlalchemy.Engine) -> int:
    """
    Executes the schema_views.sql script to build the fact_orders table/view.
    
    Args:
        engine: The SQLAlchemy engine connected to the database.
        
    Returns:
        int: The number of rows in the fact_orders table.
    """
    sql_path = PROJECT_ROOT / 'sql' / 'schema_views.sql'
    if not sql_path.exists():
        logger.error(f"SQL file not found at {sql_path}")
        raise FileNotFoundError(f"SQL file not found at {sql_path}")
        
    logger.info(f"Reading SQL script from {sql_path}")
    with open(sql_path, 'r', encoding='utf-8') as f:
        sql_script = f.read()
        
    statements = [s.strip() for s in sql_script.split(';') if s.strip()]
    
    logger.info(f"Executing {len(statements)} SQL statements")
    with engine.connect() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        conn.commit()
        
        logger.info("Successfully executed all statements. Fetching row count.")
        result = conn.execute(text("SELECT COUNT(*) FROM fact_orders")).fetchone()
        row_count = result[0] if result else 0
        
    logger.info(f"Created fact_orders with {row_count} rows")
    return row_count


def validate_fact_orders(engine: sqlalchemy.Engine) -> Dict[str, Any]:
    """
    Validates the fact_orders table by running basic data quality checks.
    
    Args:
        engine: The SQLAlchemy engine connected to the database.
        
    Returns:
        dict: A dictionary containing the validation results.
    """
    logger.info("Running validations on fact_orders table...")
    results: Dict[str, Any] = {}
    
    with engine.connect() as conn:
        # Total row count
        count_res = conn.execute(text("SELECT COUNT(*) FROM fact_orders")).fetchone()
        results["total_rows"] = count_res[0] if count_res else 0
        
        # Date range
        try:
            date_res = conn.execute(text("SELECT MIN(order_date), MAX(order_date) FROM fact_orders")).fetchone()
            if date_res:
                results["min_date"] = str(date_res[0])
                results["max_date"] = str(date_res[1])
        except Exception as e:
            logger.warning(f"Could not validate date range (column might not be named 'order_date'): {e}")
            
        # Null check
        try:
            null_res = conn.execute(text("""
                SELECT 
                    SUM(CASE WHEN order_id IS NULL THEN 1 ELSE 0 END) as null_order_id,
                    SUM(CASE WHEN order_date IS NULL THEN 1 ELSE 0 END) as null_order_date,
                    SUM(CASE WHEN price IS NULL THEN 1 ELSE 0 END) as null_price
                FROM fact_orders
            """)).fetchone()
            
            if null_res:
                results["nulls"] = {
                    "order_id": null_res[0],
                    "order_date": null_res[1],
                    "price": null_res[2]
                }
        except Exception as e:
             logger.warning(f"Could not validate nulls on expected columns: {e}")
            
        # Sample rows
        try:
            sample_res = conn.execute(text("SELECT * FROM fact_orders LIMIT 5")).fetchall()
            if sample_res:
                keys = sample_res[0]._mapping.keys()
                results["sample"] = [dict(zip(keys, row)) for row in sample_res]
        except Exception as e:
             logger.warning(f"Could not fetch sample rows: {e}")
             
    logger.info(f"Validation results: {results}")
    return results


def main() -> None:
    """
    Main function to orchestrate building and validating the fact_orders table.
    """
    resolved_url = _resolve_db_url(DB_URL, PROJECT_ROOT)
    logger.info(f"Connecting to database with URL: {resolved_url}")
    engine = create_engine(resolved_url)
    
    try:
        build_fact_orders(engine)
        validate_fact_orders(engine)
        logger.info("Successfully built and validated fact_orders.")
    except Exception as e:
        logger.error(f"Failed to build views: {e}", exc_info=True)
        raise


if __name__ == '__main__':
    main()
