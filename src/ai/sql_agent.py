import logging
import pandas as pd
from typing import Optional, Tuple
from pydantic import BaseModel
from pathlib import Path
from dotenv import load_dotenv

from src.ai.llm_client import call_llm
from src.ai import prompts
from src.safety.sql_guard import validate_sql

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / '.env')
logger = logging.getLogger(__name__)

class GeneratedSQL(BaseModel):
    query: str
    explanation: str

def generate_sql(hypothesis: str, schema_context: str, config: dict) -> GeneratedSQL:
    """Generate a SQL query to test a specific hypothesis using the provided schema context."""
    system_prompt = prompts.SYSTEM_SQL.format(schema_context=schema_context)
    user_prompt = prompts.USER_SQL.format(
        hypothesis=hypothesis,
        schema_context=schema_context
    )
    
    result = call_llm(
        system=system_prompt,
        user=user_prompt,
        response_model=GeneratedSQL,
        temperature=0.1
    )
    
    if isinstance(result, GeneratedSQL):
        is_valid, cleaned_or_err = validate_sql(result.query)
        if not is_valid:
            raise ValueError(f"SQL Guard rejected query: {cleaned_or_err}")
        result.query = cleaned_or_err
        return result
    raise ValueError("Failed to generate SQL")

def execute_with_retry(engine, hypothesis: str, schema_context: str, config: dict) -> Tuple[Optional[GeneratedSQL], Optional[pd.DataFrame], Optional[str]]:
    """Execute generated SQL against a database engine, automatically retrying upon errors or empty results."""
    from src.safety.sql_guard import execute_safe_query
    
    max_attempts = config.get("llm", {}).get("max_sql_attempts", config.get("max_sql_retries", 3))
    timeout = config.get("database", {}).get("query_timeout_seconds", 20)
    error_msg = None
    
    for attempt in range(max_attempts):
        try:
            sql_obj = generate_sql(hypothesis, schema_context, config)
            df = execute_safe_query(engine, sql_obj.query, timeout_seconds=timeout)
                
            if df.empty:
                raise ValueError("Query returned empty result")
                
            return sql_obj, df, None
            
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"SQL generation/execution failed on attempt {attempt + 1}: {e}")
            hypothesis = f"{hypothesis}\nPrevious query failed with: {error_msg}. Please fix the query."
            
    return None, None, error_msg
