import io
import os
import re
import logging
import json
from typing import Literal, Optional, List, Tuple, Any
from pathlib import Path
from dotenv import load_dotenv
import pandas as pd
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / '.env')
logger = logging.getLogger(__name__)

from src.analysis.stats_tests import TestResult

class Evidence(BaseModel):
    """Evidence for a hypothesis verification."""
    hypothesis_id: str
    query_id: str
    sql: str
    result_df_json: str
    test_result: Optional[TestResult] = None
    verdict: Literal['SUPPORTED', 'REJECTED', 'INCONCLUSIVE']

class VerificationResult(BaseModel):
    """Result of verifying hypotheses."""
    evidences: List[Evidence]
    verification_score: float
    unmatched_numbers: List[str]

def verify_hypotheses(engine, hypotheses: list, sql_results: list[tuple], config: dict) -> list[Evidence]:
    """
    Executes statistical tests to verify hypotheses based on SQL results.
    
    Args:
        engine: SQLAlchemy engine.
        hypotheses (list): List of hypotheses.
        sql_results (list[tuple]): List of tuples (hypothesis_id, query_id, sql, dataframe).
        config (dict): Configuration dictionary with alpha and min_contribution_pct.
        
    Returns:
        list[Evidence]: A list of verified evidence objects.
    """
    evidences = []
    alpha = config.get("alpha", 0.05)
    min_contribution_pct = config.get("min_contribution_pct", 5.0)
    
    for h_id, q_id, sql, df in sql_results:
        try:
            test_result = run_statistical_test(df, config)
            verdict: Literal['SUPPORTED', 'REJECTED', 'INCONCLUSIVE'] = 'INCONCLUSIVE'
            
            if test_result.p_value < alpha and test_result.contribution >= min_contribution_pct:
                verdict = 'SUPPORTED'
            elif test_result.p_value >= alpha:
                verdict = 'REJECTED'
                
            evidence = Evidence(
                hypothesis_id=h_id,
                query_id=q_id,
                sql=sql,
                result_df_json=df.to_json(orient='records'),
                test_result=test_result,
                verdict=verdict
            )
            evidences.append(evidence)
        except Exception as e:
            logger.error(f"Error verifying hypothesis {h_id}: {e}")
            evidences.append(Evidence(
                hypothesis_id=h_id, query_id=q_id, sql=sql, result_df_json=df.to_json(),
                verdict='INCONCLUSIVE'
            ))
            
    return evidences

def extract_numbers(text: str) -> list[float]:
    """Extracts business metric numbers from a string, ignoring code identifiers, dates, query IDs, and markdown numbering."""
    # 1. Remove inline code and IDs in backticks (e.g. `6560211a19...`)
    clean = re.sub(r'`[^`]+`', '', text)
    # 2. Remove ISO dates like 2018-05-07
    clean = re.sub(r'\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b', '', clean)
    # 3. Remove query and hypothesis references like Q1, Q2, H1, H2, [Query ID: Q3]
    clean = re.sub(r'\[?(?:Query\s*ID:?\s*)?[QH]\d+\]?', '', clean, flags=re.IGNORECASE)
    # 4. Remove list item enumerations like "\n1. ", "\n2. ", "1. " and headings
    clean = re.sub(r'(?:^|\n)\s*\d+\.\s+', '\n', clean)
    clean = re.sub(r'(?:^|\n)#+\s*\d+\.?\s*', '\n', clean)
    # 5. Remove currency symbols and formatting commas
    clean = clean.replace('$', '').replace(',', '')
    # 6. Match standalone numbers bounded by non-alphanumeric characters
    pattern = r'(?<![a-zA-Z0-9_])-?\d+(?:\.\d+)?(?![a-zA-Z0-9_])'
    matches = re.findall(pattern, clean)
    return [float(m) for m in matches if abs(float(m)) > 1e-6]

def verify_report_numbers(
    report_text: str, 
    evidences: list[Evidence], 
    anomaly: Any = None,
    tolerance_pct: float = 1.0
) -> tuple[bool, list[str]]:
    """
    Extracts numbers from report and verifies if they exist in the evidence results or anomaly metadata.
    """
    report_numbers = extract_numbers(report_text)
    
    evidence_numbers = [0.0, 1.0, 0.05, 0.01, 0.10]
    
    # Include numbers from anomaly context
    if anomaly is not None:
        anom_dict = anomaly.model_dump() if hasattr(anomaly, 'model_dump') else (anomaly if isinstance(anomaly, dict) else vars(anomaly))
        for k in ['actual', 'expected', 'pct_change', 'z_score']:
            val = anom_dict.get(k)
            if val is not None and isinstance(val, (int, float)):
                evidence_numbers.append(float(val))

    for ev in evidences:
        try:
            df = pd.read_json(io.StringIO(ev.result_df_json))
            for col in df.select_dtypes(include=['number']).columns:
                evidence_numbers.extend(df[col].dropna().tolist())
                
            if ev.test_result:
                evidence_numbers.append(ev.test_result.p_value)
                if hasattr(ev.test_result, 'effect_size'):
                    evidence_numbers.append(ev.test_result.effect_size)
                if hasattr(ev.test_result, 'statistic'):
                    evidence_numbers.append(ev.test_result.statistic)
                if hasattr(ev.test_result, 'ci_lower'):
                    evidence_numbers.append(ev.test_result.ci_lower)
                if hasattr(ev.test_result, 'ci_upper'):
                    evidence_numbers.append(ev.test_result.ci_upper)
                if hasattr(ev.test_result, 'interpretation'):
                    evidence_numbers.extend(extract_numbers(ev.test_result.interpretation))
        except Exception as e:
            logger.error(f"Error extracting numbers from evidence: {e}")
            
    unmatched = []
    for num in report_numbers:
        matched = False
        for ev_num in evidence_numbers:
            if abs(num - ev_num) < 0.05:  # Absolute tolerance for small decimals / rounding
                matched = True
                break
            elif ev_num != 0:
                diff_pct = abs((num - ev_num) / ev_num) * 100
                if diff_pct <= tolerance_pct:
                    matched = True
                    break
        if not matched:
            unmatched.append(str(num))
            
    return len(unmatched) == 0, unmatched

def verify_and_fix(reporter_fn, anomaly: Any, evidences: list[Evidence], config: dict, max_rounds: int = 2) -> tuple[str, VerificationResult]:
    """
    Generates a report, verifies facts/numbers, and attempts to fix issues iteratively.
    """
    report_text = ""
    unmatched = []
    feedback = ""
    
    for round_num in range(max_rounds):
        # Call reporter to generate report
        report_text = reporter_fn(anomaly, evidences, config, feedback=feedback)
        
        # Verify numbers against both evidences and anomaly metadata
        is_valid, unmatched = verify_report_numbers(report_text, evidences, anomaly=anomaly)
        
        if is_valid:
            break
            
        feedback = f"The following numbers were not found in the evidence: {unmatched}. Please fix and rely only on evidence data."
        logger.warning(f"Round {round_num + 1} mismatches: {unmatched}")
        
    score = 1.0 if not unmatched else max(0.0, 1.0 - (len(unmatched) * 0.1))
    
    result = VerificationResult(
        evidences=evidences,
        verification_score=score,
        unmatched_numbers=unmatched
    )
    return report_text, result
