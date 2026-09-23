"""
Contribution analysis module for Metric Detective.
Compares anomaly period vs baseline period and computes each segment's
contribution to the overall KPI change across configured dimensions.
"""

import os
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

import yaml
from pydantic import BaseModel
import pandas as pd
from sqlalchemy import text, Engine
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / '.env')
logger = logging.getLogger(__name__)


class SegmentContribution(BaseModel):
    """Represents the contribution of a single segment to the overall KPI change."""
    dimension: str
    segment: str
    baseline_value: float
    anomaly_value: float
    delta: float
    contribution_pct: float
    direction: str
    mix_effect: Optional[float] = None
    rate_effect: Optional[float] = None


class ContributionResult(BaseModel):
    """The full result of a contribution analysis."""
    kpi: str
    anomaly_period: Tuple[str, str]
    baseline_period: Tuple[str, str]
    total_delta: float
    segments: List[SegmentContribution]


def _get_baseline_dates(anomaly_start: str, anomaly_end: str, num_periods: int) -> Tuple[str, str]:
    """Calculate the baseline period spanning exactly num_periods of the anomaly length."""
    a_start = datetime.strptime(anomaly_start, '%Y-%m-%d')
    a_end = datetime.strptime(anomaly_end, '%Y-%m-%d')
    delta_days = (a_end - a_start).days + 1
    
    b_end = a_start - timedelta(days=1)
    b_start = b_end - timedelta(days=(delta_days * num_periods) - 1)
    
    return b_start.strftime('%Y-%m-%d'), b_end.strftime('%Y-%m-%d')


def compute_contributions(
    engine: Engine,
    kpi_name: str,
    anomaly_start: str,
    anomaly_end: str,
    config: dict
) -> ContributionResult:
    """
    Compute dimensional contribution to KPI anomalies by comparing the anomaly period
    to a baseline period consisting of the previous N periods.
    """
    try:
        kpi_config = config['kpis'][kpi_name]
        kpi_type = kpi_config.get('type', 'additive')
        sql_expr = kpi_config.get('sql_expr', 'COUNT(DISTINCT order_id)')
        
        ca_config = config.get('contribution_analysis', {})
        num_periods = ca_config.get('baseline_periods', 4)
        
        b_start_str, b_end_str = _get_baseline_dates(anomaly_start, anomaly_end, num_periods)
        dimensions = config.get('dimensions', [])
        
        # 1. Total KPI values
        total_query = text(f"""
            WITH anomaly AS (
                SELECT {sql_expr} AS val
                FROM fact_orders
                WHERE order_date BETWEEN :a_start AND :a_end
            ),
            baseline AS (
                SELECT CAST({sql_expr} AS FLOAT) / :n_periods AS val
                FROM fact_orders
                WHERE order_date BETWEEN :b_start AND :b_end
            )
            SELECT 
                COALESCE(b.val, 0.0) AS baseline_val,
                COALESCE(a.val, 0.0) AS anomaly_val
            FROM anomaly a CROSS JOIN baseline b
        """)
        
        with engine.connect() as conn:
            tot_row = conn.execute(total_query, {
                'a_start': anomaly_start,
                'a_end': anomaly_end,
                'b_start': b_start_str,
                'b_end': b_end_str,
                'n_periods': num_periods
            }).fetchone()
            
            total_baseline = float(tot_row[0] or 0.0)
            total_anomaly = float(tot_row[1] or 0.0)
            total_delta = total_anomaly - total_baseline
            
            all_segments: List[SegmentContribution] = []
            
            # 2. Iterate each dimension
            for dim_config in dimensions:
                dim_name = dim_config['name']
                top_n = dim_config.get('top_n')
                
                # Check for ratio / rate breakdown
                if kpi_type == 'ratio' and 'numerator' in kpi_config and 'denominator' in kpi_config:
                    num_expr = kpi_config['numerator']
                    den_expr = kpi_config['denominator']
                    
                    dim_query = text(f"""
                        WITH anomaly AS (
                            SELECT 
                                {dim_name} AS segment,
                                CAST({num_expr} AS FLOAT) AS num_val,
                                CAST({den_expr} AS FLOAT) AS den_val
                            FROM fact_orders
                            WHERE order_date BETWEEN :a_start AND :a_end AND {dim_name} IS NOT NULL
                            GROUP BY {dim_name}
                        ),
                        baseline AS (
                            SELECT 
                                {dim_name} AS segment,
                                CAST({num_expr} AS FLOAT) / :n_periods AS num_val,
                                CAST({den_expr} AS FLOAT) / :n_periods AS den_val
                            FROM fact_orders
                            WHERE order_date BETWEEN :b_start AND :b_end AND {dim_name} IS NOT NULL
                            GROUP BY {dim_name}
                        )
                        SELECT 
                            COALESCE(a.segment, b.segment) AS segment,
                            COALESCE(b.num_val, 0.0) AS b_num,
                            COALESCE(b.den_val, 0.0) AS b_den,
                            COALESCE(a.num_val, 0.0) AS a_num,
                            COALESCE(a.den_val, 0.0) AS a_den
                        FROM anomaly a
                        FULL OUTER JOIN baseline b ON a.segment = b.segment
                    """)
                    
                    rows = conn.execute(dim_query, {
                        'a_start': anomaly_start,
                        'a_end': anomaly_end,
                        'b_start': b_start_str,
                        'b_end': b_end_str,
                        'n_periods': num_periods
                    }).fetchall()
                    
                    tot_b_den = sum(float(r[2] or 0.0) for r in rows)
                    tot_a_den = sum(float(r[4] or 0.0) for r in rows)
                    
                    for r in rows:
                        segment = str(r[0])
                        b_num, b_den = float(r[1] or 0.0), float(r[2] or 0.0)
                        a_num, a_den = float(r[3] or 0.0), float(r[4] or 0.0)
                        
                        b_rate = (b_num / b_den) if b_den > 0 else 0.0
                        a_rate = (a_num / a_den) if a_den > 0 else 0.0
                        
                        b_share = (b_den / tot_b_den) if tot_b_den > 0 else 0.0
                        a_share = (a_den / tot_a_den) if tot_a_den > 0 else 0.0
                        
                        rate_effect = b_share * (a_rate - b_rate)
                        mix_effect = (a_share - b_share) * b_rate
                        seg_delta = rate_effect + mix_effect
                        
                        contrib_pct = (seg_delta / total_delta * 100) if total_delta != 0 else 0.0
                        direction = "increase" if seg_delta > 0 else "decrease"
                        
                        all_segments.append(SegmentContribution(
                            dimension=dim_name,
                            segment=segment,
                            baseline_value=b_rate,
                            anomaly_value=a_rate,
                            delta=seg_delta,
                            contribution_pct=contrib_pct,
                            direction=direction,
                            mix_effect=mix_effect,
                            rate_effect=rate_effect
                        ))
                else:
                    # Additive or standard average KPI
                    dim_query = text(f"""
                        WITH anomaly AS (
                            SELECT 
                                {dim_name} AS segment,
                                {sql_expr} AS val
                            FROM fact_orders
                            WHERE order_date BETWEEN :a_start AND :a_end AND {dim_name} IS NOT NULL
                            GROUP BY {dim_name}
                        ),
                        baseline AS (
                            SELECT 
                                {dim_name} AS segment,
                                CAST({sql_expr} AS FLOAT) / :n_periods AS val
                            FROM fact_orders
                            WHERE order_date BETWEEN :b_start AND :b_end AND {dim_name} IS NOT NULL
                            GROUP BY {dim_name}
                        )
                        SELECT 
                            COALESCE(a.segment, b.segment) AS segment,
                            COALESCE(b.val, 0.0) AS baseline_val,
                            COALESCE(a.val, 0.0) AS anomaly_val
                        FROM anomaly a
                        FULL OUTER JOIN baseline b ON a.segment = b.segment
                    """)
                    
                    rows = conn.execute(dim_query, {
                        'a_start': anomaly_start,
                        'a_end': anomaly_end,
                        'b_start': b_start_str,
                        'b_end': b_end_str,
                        'n_periods': num_periods
                    }).fetchall()
                    
                    dim_segments = []
                    for r in rows:
                        segment = str(r[0])
                        b_val = float(r[1] or 0.0)
                        a_val = float(r[2] or 0.0)
                        delta = a_val - b_val
                        contrib_pct = (delta / total_delta * 100) if total_delta != 0 else 0.0
                        direction = "increase" if delta > 0 else "decrease"
                        
                        dim_segments.append(SegmentContribution(
                            dimension=dim_name,
                            segment=segment,
                            baseline_value=b_val,
                            anomaly_value=a_val,
                            delta=delta,
                            contribution_pct=contrib_pct,
                            direction=direction
                        ))
                    
                    if top_n and len(dim_segments) > top_n:
                        dim_segments.sort(key=lambda x: abs(x.delta), reverse=True)
                        dim_segments = dim_segments[:top_n]
                        
                    all_segments.extend(dim_segments)
                    
        # Sort all segments by absolute contribution descending
        all_segments.sort(key=lambda x: abs(x.contribution_pct), reverse=True)
        
        return ContributionResult(
            kpi=kpi_name,
            anomaly_period=(anomaly_start, anomaly_end),
            baseline_period=(b_start_str, b_end_str),
            total_delta=total_delta,
            segments=all_segments
        )
    except Exception as e:
        logger.error(f"Error computing contributions for {kpi_name}: {e}")
        raise


def format_contribution_report(result: ContributionResult) -> str:
    """Format a contribution result into a readable markdown report."""
    report = [
        f"## Contribution Analysis Report: {result.kpi}",
        f"**Anomaly Period:** {result.anomaly_period[0]} to {result.anomaly_period[1]}",
        f"**Baseline Period:** {result.baseline_period[0]} to {result.baseline_period[1]}",
        f"**Total Delta:** {result.total_delta:,.2f}",
        "",
        "### Top Contributors",
        "| Dimension | Segment | Baseline | Anomaly | Delta | Contribution % | Direction |",
        "|-----------|---------|----------|---------|-------|----------------|-----------|"
    ]
    
    for seg in result.segments[:15]:
        report.append(
            f"| {seg.dimension} | {seg.segment} | {seg.baseline_value:,.2f} | "
            f"{seg.anomaly_value:,.2f} | {seg.delta:,.2f} | {seg.contribution_pct:.1f}% | {seg.direction} |"
        )
        
    return "\n".join(report)
