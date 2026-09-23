import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Union
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / '.env')
logger = logging.getLogger(__name__)

from src.ai.verifier import Evidence
from src.ai.llm_client import call_llm
from src.analysis.contribution import ContributionResult


def generate_report(anomaly: Any, evidences: List[Evidence], config: dict, feedback: str = "") -> str:
    """
    Generates an evidence-backed markdown report using the LLM.
    Strictly forbids inventing numbers: every claim must be traceable to query results.
    """
    anomaly_dict = anomaly.model_dump() if hasattr(anomaly, 'model_dump') else (anomaly if isinstance(anomaly, dict) else vars(anomaly))
    valid_evidences = [e for e in evidences if e.verdict in ('SUPPORTED', 'INCONCLUSIVE')]
    
    system_prompt = """You are a senior data root-cause analyst at Metric Detective.
CRITICAL NON-NEGOTIABLE RULE: NEVER invent or hallucinate numbers. Every single number, percentage, or dollar amount in your narrative MUST come directly from the provided evidence or the anomaly object.
Footnote every claim with its Query ID (e.g. [Query ID: Q1]).

Generate a clean markdown report with these EXACT sections:
## Headline
(Exactly 1 concise sentence summarizing the anomaly and main root cause)

## What Happened
(State the metric, anomaly date/period, actual vs expected values, % change, and statistical severity)

## Root Causes (Ranked by Contribution %)
(Numbered list of root causes ranked from highest to lowest % contribution. For each, give segment name, contribution %, and cite [Query ID: ...])

## Evidence Details
(Detailed discussion of queries, metrics, and statistical test results with [Query ID: ...])

## Rejected Hypotheses
(Bullet list of hypotheses that were tested and rejected or showed no statistical significance)

## Recommended Actions
(Specific, actionable business recommendations for operations, pricing, or product teams)

## Confidence Level
(High / Medium / Low with explicit justification based on p-values and sample sizes)
"""

    evidence_summary = []
    for ev in evidences:
        ev_item = {
            "hypothesis_id": ev.hypothesis_id,
            "query_id": ev.query_id,
            "verdict": ev.verdict,
            "sql": ev.sql,
            "data_sample": json.loads(ev.result_df_json)[:10] if ev.result_df_json else []
        }
        if ev.test_result:
            ev_item["test_result"] = ev.test_result.model_dump() if hasattr(ev.test_result, 'model_dump') else vars(ev.test_result)
        evidence_summary.append(ev_item)

    user_prompt = f"""Anomaly Information:
{json.dumps(anomaly_dict, default=str, indent=2)}

Verified Evidence Store:
{json.dumps(evidence_summary, default=str, indent=2)}
"""
    if feedback:
        user_prompt += f"\n\nCRITICAL FIX REQUIRED FROM VERIFIER:\n{feedback}\nPlease fix the numbers so that every number matches the evidence store exactly."

    try:
        report = call_llm(system=system_prompt, user=user_prompt, temperature=0.2)
        return str(report)
    except Exception as e:
        logger.error(f"LLM generation failed, falling back to deterministic report: {e}")
        return generate_contribution_only_report(anomaly, evidences, config)


def save_report(report_text: str, anomaly: Any, evidences: List[Evidence], output_dir: Path) -> Path:
    """Saves the markdown report and evidence JSON file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    kpi = getattr(anomaly, 'kpi', None) or (anomaly.get('kpi') if isinstance(anomaly, dict) else 'kpi')
    
    md_path = output_dir / f"{timestamp}_{kpi}.md"
    json_path = output_dir / f"{timestamp}_{kpi}_evidence.json"
    
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(report_text)
        
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump([e.model_dump() for e in evidences], f, default=str, indent=2)
        
    logger.info(f"Report saved to {md_path}")
    return md_path


def generate_contribution_only_report(anomaly: Any, evidences_or_contrib: Any, config: dict) -> str:
    """Deterministic fallback report without LLM."""
    anomaly_dict = anomaly.model_dump() if hasattr(anomaly, 'model_dump') else (anomaly if isinstance(anomaly, dict) else vars(anomaly))
    kpi = anomaly_dict.get('kpi', 'KPI')
    dt = anomaly_dict.get('date', 'Unknown')
    act = anomaly_dict.get('actual', 0.0)
    exp = anomaly_dict.get('expected', 0.0)
    pct = anomaly_dict.get('pct_change', 0.0)
    
    report = f"## Headline\nAnomaly detected in {kpi} on {dt}: actual value of {act:,.2f} deviated by {pct:.1f}% from expected {exp:,.2f}.\n\n"
    report += "## What Happened\n"
    report += f"The KPI '{kpi}' recorded an actual value of {act:,.2f} versus an expected baseline of {exp:,.2f} ({pct:+.1f}% change).\n\n"
    
    report += "## Root Causes (Ranked by Contribution %)\n"
    if isinstance(evidences_or_contrib, list):
        supported = [e for e in evidences_or_contrib if getattr(e, 'verdict', None) == 'SUPPORTED']
        if supported:
            for i, e in enumerate(supported, 1):
                report += f"{i}. Hypothesis {e.hypothesis_id}: supported by query evidence [Query ID: {e.query_id}]\n"
        else:
            report += "No hypotheses met the statistical significance threshold for supported root cause.\n"
    elif hasattr(evidences_or_contrib, 'segments'):
        for i, s in enumerate(evidences_or_contrib.segments[:5], 1):
            report += f"{i}. {s.dimension} = '{s.segment}': {s.contribution_pct:.1f}% contribution (delta: {s.delta:,.2f}) [Query ID: contrib_engine]\n"
    else:
        report += "Contribution analysis completed; see raw outputs for segment breakdown.\n"
        
    report += "\n## Evidence Details\nDeterministic calculation computed from fact_orders and kpi_daily without LLM generation.\n"
    report += "\n## Rejected Hypotheses\nNone identified during deterministic pass.\n"
    report += "\n## Recommended Actions\n1. Review dimensional contributors highlighted in the root cause table.\n2. Verify upstream data ingestion for affected segments.\n"
    report += "\n## Confidence Level\nMedium (Deterministic Contribution Analysis)\n"
    
    return report
