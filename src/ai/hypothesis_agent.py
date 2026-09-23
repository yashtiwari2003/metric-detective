import logging
from typing import Literal, List, Any
from pydantic import BaseModel
from pathlib import Path
from dotenv import load_dotenv

from src.ai.llm_client import call_llm
from src.ai import prompts
from src.detection.anomaly import Anomaly

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / '.env')
logger = logging.getLogger(__name__)

class Hypothesis(BaseModel):
    id: str
    statement: str
    dimensions: List[str]
    expected_signal: str
    test_type: Literal['ttest', 'proportion_ztest', 'chi_square']
    priority: int

class HypothesisSet(BaseModel):
    hypotheses: List[Hypothesis]

def generate_hypotheses(anomaly: Anomaly, schema_context: str, contribution_summary: str, config: dict) -> HypothesisSet:
    """Generate multiple hypotheses for a given anomaly based on schema and contribution contexts."""
    system_prompt = prompts.SYSTEM_HYPOTHESIS
    user_prompt = prompts.USER_HYPOTHESIS.format(
        anomaly_summary=str(anomaly),
        schema_context=schema_context,
        contribution_summary=contribution_summary
    )
    
    max_retries = 2
    for attempt in range(max_retries):
        try:
            result = call_llm(
                system=system_prompt,
                user=user_prompt,
                response_model=HypothesisSet,
                temperature=0.7
            )
            
            if not isinstance(result, HypothesisSet):
                continue
                
            if len(result.hypotheses) < 4 or len(result.hypotheses) > 10:
                raise ValueError(f"Generated {len(result.hypotheses)} hypotheses. Expected between 4 and 8.")
                
            return result
        except Exception as e:
            logger.warning(f"Hypothesis generation/validation failed on attempt {attempt}: {e}")
            if attempt == max_retries - 1:
                raise
                
    raise ValueError("Failed to generate valid hypotheses")
