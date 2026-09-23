"""
Orchestrator pipeline for Metric Detective.
Runs the complete root-cause analysis workflow:
anomaly -> hypotheses -> SQL queries + execution -> statistical tests -> verification -> report.
"""

import os
import sys
import json
import logging
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Tuple, Dict, Any, List, Optional

import yaml
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / '.env')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s'
)
logger = logging.getLogger(__name__)

from src.detection.anomaly import Anomaly, detect_anomalies, detect_all_anomalies
from src.analysis.contribution import compute_contributions, format_contribution_report, ContributionResult
from src.analysis.stats_tests import welch_ttest, proportion_ztest, chi_square_test, TestResult
from src.ai.llm_client import call_llm, get_usage_stats
from src.ai.schema_context import build_schema_context
from src.ai.hypothesis_agent import generate_hypotheses, Hypothesis, HypothesisSet
from src.ai.sql_agent import execute_with_retry, GeneratedSQL
from src.safety.sql_guard import validate_sql, execute_safe_query
from src.ai.verifier import Evidence, VerificationResult, verify_report_numbers, verify_and_fix
from src.ai.reporter import generate_report, save_report, generate_contribution_only_report


def load_config() -> dict:
    """Load configuration from config.yaml."""
    config_path = PROJECT_ROOT / 'config.yaml'
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def get_engine():
    """Create SQLAlchemy engine from DB_URL."""
    db_url = os.getenv('DB_URL', 'duckdb:///data/metric.duckdb')
    if db_url.startswith('duckdb:///'):
        db_path = db_url.replace('duckdb:///', '')
        db_full_path = PROJECT_ROOT / db_path
        db_full_path.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"duckdb:///{db_full_path}"
    return create_engine(db_url)


def run_matching_stats_test(hypothesis: Hypothesis, df: pd.DataFrame, config: dict) -> Optional[TestResult]:
    """Execute statistical test according to hypothesis specification and query output."""
    try:
        alpha = config.get('statistical_tests', {}).get('significance_level', 0.05)
        num_cols = df.select_dtypes(include=['number']).columns.tolist()
        
        if hypothesis.test_type == 'ttest' and len(num_cols) >= 1:
            val_col = num_cols[0]
            vals = df[val_col].dropna().tolist()
            if len(vals) >= 4:
                half = len(vals) // 2
                return welch_ttest(vals[:half], vals[half:], alpha=alpha)
                
        elif hypothesis.test_type == 'proportion_ztest' and len(num_cols) >= 2:
            s_col, t_col = num_cols[0], num_cols[1]
            if len(df) >= 2:
                b_succ = int(df.iloc[0][s_col])
                b_tot = int(df.iloc[0][t_col])
                a_succ = int(df.iloc[1][s_col])
                a_tot = int(df.iloc[1][t_col])
                return proportion_ztest(b_succ, b_tot, a_succ, a_tot, alpha=alpha)
                
        elif hypothesis.test_type == 'chi_square' and len(df.columns) >= 2:
            cat_col = df.select_dtypes(include=['object']).columns
            if len(cat_col) > 0 and len(num_cols) > 0:
                counts = dict(zip(df[cat_col[0]].astype(str), df[num_cols[0]]))
                half = len(counts) // 2
                keys = list(counts.keys())
                b_cnt = {k: counts[k] for k in keys[:half]}
                a_cnt = {k: counts[k] for k in keys[half:]}
                return chi_square_test(b_cnt, a_cnt, alpha=alpha)
    except Exception as e:
        logger.warning(f"Stats test execution fallback for {hypothesis.id}: {e}")
        
    return None


def investigate_anomaly(engine, anomaly: Anomaly, config: dict) -> Tuple[str, dict]:
    """
    Run full root cause investigation on an anomaly.
    Returns (report_markdown, metadata_dict).
    """
    logger.info(f"=== Starting Investigation: {anomaly.kpi} on {anomaly.date} ===")
    
    # 1. Anomaly date range (default 7 days window ending on anomaly date)
    end_date = anomaly.date.strftime('%Y-%m-%d')
    start_date = (anomaly.date - timedelta(days=6)).strftime('%Y-%m-%d')
    
    # 2. Contribution Analysis (deterministic backbone)
    logger.info("Step 1: Running dimensional contribution analysis...")
    contrib_result = compute_contributions(engine, anomaly.kpi, start_date, end_date, config)
    contrib_summary = format_contribution_report(contrib_result)
    
    # 3. Schema Context
    logger.info("Step 2: Building compact schema context...")
    schema_context = build_schema_context(engine)
    
    # 4. Generate Hypotheses
    logger.info("Step 3: AI Hypothesis Agent generating hypotheses...")
    try:
        hypo_set = generate_hypotheses(anomaly, schema_context, contrib_summary, config)
        hypotheses = hypo_set.hypotheses
    except Exception as e:
        logger.error(f"Hypothesis generation failed: {e}. Falling back to contribution report.")
        fallback_report = generate_contribution_only_report(anomaly, contrib_result, config)
        save_report(fallback_report, anomaly, [], PROJECT_ROOT / 'reports')
        return fallback_report, {"fallback": True, "error": str(e)}
        
    logger.info(f"Generated {len(hypotheses)} hypotheses.")
    
    # 5. SQL Generation and Execution
    logger.info("Step 4: AI SQL Agent generating & executing queries with safety guard...")
    evidences: List[Evidence] = []
    
    for i, h in enumerate(hypotheses, 1):
        q_id = f"Q{i}"
        logger.info(f"Testing [{h.id}]: {h.statement}")
        sql_obj, df, err = execute_with_retry(engine, h.statement, schema_context, config)
        
        if df is not None and not df.empty:
            test_res = run_matching_stats_test(h, df, config)
            verdict = 'SUPPORTED' if (test_res and test_res.significant) else ('INCONCLUSIVE' if test_res is None else 'REJECTED')
            
            evidences.append(Evidence(
                hypothesis_id=h.id,
                query_id=q_id,
                sql=sql_obj.query if sql_obj else "",
                result_df_json=df.to_json(orient='records'),
                test_result=test_res,
                verdict=verdict
            ))
        else:
            evidences.append(Evidence(
                hypothesis_id=h.id,
                query_id=q_id,
                sql=sql_obj.query if sql_obj else "",
                result_df_json="[]",
                test_result=None,
                verdict='REJECTED'
            ))
            
    # 6. Verification and Report Generation with Self-Correction
    logger.info("Step 5: Verifying facts and generating evidence-backed report...")
    report_text, verif_result = verify_and_fix(
        reporter_fn=generate_report,
        anomaly=anomaly,
        evidences=evidences,
        config=config,
        max_rounds=config.get('verifier', {}).get('max_fix_rounds', 2)
    )
    
    # 7. Save Artifacts
    output_dir = PROJECT_ROOT / config.get('report', {}).get('output_dir', 'reports')
    saved_path = save_report(report_text, anomaly, evidences, output_dir)
    
    usage = get_usage_stats()
    logger.info(f"Investigation complete. Verification Score: {verif_result.verification_score:.2f}")
    logger.info(f"LLM Usage: {usage['total_calls']} calls, {usage['total_input_tokens']} in, {usage['total_output_tokens']} out, ${usage['total_cost']:.4f}")
    
    metadata = {
        "kpi": anomaly.kpi,
        "date": str(anomaly.date),
        "actual": anomaly.actual,
        "expected": anomaly.expected,
        "pct_change": anomaly.pct_change,
        "verification_score": verif_result.verification_score,
        "unmatched_numbers": verif_result.unmatched_numbers,
        "report_path": str(saved_path),
        "usage": usage
    }
    
    return report_text, metadata


def investigate_period(engine, kpi: str, start: str, end: str, config: dict) -> Tuple[str, dict]:
    """Investigate a user-specified date period for a KPI."""
    with engine.connect() as conn:
        row = conn.execute(text(f"""
            SELECT AVG({kpi}) FROM kpi_daily WHERE order_date BETWEEN :s AND :e
        """), {'s': start, 'e': end}).fetchone()
        act_val = float(row[0] or 0.0)
        
        base_row = conn.execute(text(f"""
            SELECT AVG({kpi}) FROM kpi_daily
        """)).fetchone()
        exp_val = float(base_row[0] or act_val)
        
    pct_chg = ((act_val - exp_val) / exp_val * 100) if exp_val != 0 else 0.0
    anom_date = datetime.strptime(end, '%Y-%m-%d').date()
    
    synthetic_anomaly = Anomaly(
        kpi=kpi,
        date=anom_date,
        actual=act_val,
        expected=exp_val,
        pct_change=pct_chg,
        z_score=3.5 if pct_chg > 0 else -3.5,
        direction='increase' if pct_chg > 0 else 'decrease'
    )
    return investigate_anomaly(engine, synthetic_anomaly, config)


def main():
    parser = argparse.ArgumentParser(description="Metric Detective - AI Root-Cause Analyst")
    parser.add_argument('--kpi', type=str, default='revenue', help="KPI to investigate (revenue, orders, etc.)")
    parser.add_argument('--start', type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument('--end', type=str, help="End date (YYYY-MM-DD)")
    parser.add_argument('--auto', action='store_true', help="Auto-detect anomalies and investigate top 3")
    parser.add_argument('--provider', type=str, help="Override LLM provider")
    parser.add_argument('--model', type=str, help="Override LLM model")
    
    args = parser.parse_args()
    config = load_config()
    engine = get_engine()
    
    if args.provider:
        os.environ['LLM_PROVIDER'] = args.provider
    if args.model:
        os.environ['LLM_MODEL'] = args.model
        
    if args.auto:
        logger.info("Running automatic detection across all KPIs...")
        all_anomalies = detect_all_anomalies(engine, config)
        flat_anomalies = []
        for kpi, anoms in all_anomalies.items():
            flat_anomalies.extend(anoms)
            
        # Sort by absolute z-score descending
        flat_anomalies.sort(key=lambda a: abs(a.z_score), reverse=True)
        top_anomalies = flat_anomalies[:3]
        
        logger.info(f"Top 3 anomalies selected for investigation:")
        for a in top_anomalies:
            logger.info(f" - {a.kpi} on {a.date}: z={a.z_score:.2f}, change={a.pct_change:.1f}%")
            
        for a in top_anomalies:
            report, meta = investigate_anomaly(engine, a, config)
            print("\n" + "="*80)
            print(f"REPORT: {a.kpi} ({a.date})")
            print("="*80)
            print(report[:600] + "\n...\n")
    elif args.start and args.end:
        report, meta = investigate_period(engine, args.kpi, args.start, args.end, config)
        print("\n" + "="*80)
        print(f"REPORT: {args.kpi} ({args.start} to {args.end})")
        print("="*80)
        print(report)
    else:
        # Default: investigate the highest z-score anomaly for the specified KPI
        anomalies = detect_anomalies(engine, args.kpi, config)
        if not anomalies:
            logger.warning(f"No anomalies detected for KPI {args.kpi}")
            return
        anomalies.sort(key=lambda a: abs(a.z_score), reverse=True)
        top_a = anomalies[0]
        report, meta = investigate_anomaly(engine, top_a, config)
        print("\n" + "="*80)
        print(f"REPORT: {top_a.kpi} ({top_a.date})")
        print("="*80)
        print(report)


if __name__ == '__main__':
    main()
