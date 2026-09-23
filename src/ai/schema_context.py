import logging
from typing import List
from pathlib import Path
from sqlalchemy import text
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / '.env')
logger = logging.getLogger(__name__)

def build_schema_context(engine) -> str:
    """Build a compact schema context representation for LLM prompts."""
    tables = get_valid_tables(engine)
    context_lines = []
    
    with engine.connect() as conn:
        for table in tables:
            try:
                count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                
                columns_result = conn.execute(text(f"DESCRIBE {table}")).fetchall()
                col_defs = []
                for row in columns_result:
                    col_name = row[0]
                    col_type = row[1]
                    
                    samples = []
                    if table in ['fact_orders', 'kpi_daily']:
                        samples_result = conn.execute(text(f"SELECT {col_name} FROM {table} WHERE {col_name} IS NOT NULL LIMIT 3")).fetchall()
                        samples = [str(s[0]) for s in samples_result]
                    
                    sample_str = f", samples: {', '.join(samples)}" if samples else ""
                    col_defs.append(f"{col_name} ({col_type}{sample_str})")
                
                context_lines.append(f"TABLE: {table} ({count} rows)")
                context_lines.append(f"COLUMNS: {', '.join(col_defs)}")
                context_lines.append("")
                
            except Exception as e:
                logger.error(f"Error extracting schema for {table}: {e}")
                
    # Cap roughly at 2000 tokens (approx 6000-8000 chars) to save context window
    return "\n".join(context_lines)[:6000]

def get_valid_tables(engine) -> List[str]:
    """Get list of tables available in the database."""
    with engine.connect() as conn:
        result = conn.execute(text("SHOW TABLES")).fetchall()
        return [row[0] for row in result]

def get_valid_columns(engine, table_name: str) -> List[str]:
    """Get valid columns for a specific table."""
    with engine.connect() as conn:
        result = conn.execute(text(f"DESCRIBE {table_name}")).fetchall()
        return [row[0] for row in result]
