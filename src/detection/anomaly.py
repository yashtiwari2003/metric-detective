"""
Anomaly detection module using STL decomposition + robust z-score.
"""

import os
import logging
from pathlib import Path
from typing import Literal, Dict, List
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from statsmodels.tsa.seasonal import STL
from pydantic import BaseModel
import sqlalchemy as sa
import yaml
from dotenv import load_dotenv
from datetime import date

# Set up project root and environment
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / '.env')
logger = logging.getLogger(__name__)

class Anomaly(BaseModel):
    """Pydantic model representing a detected anomaly."""
    kpi: str
    date: date
    actual: float
    expected: float
    pct_change: float
    z_score: float
    direction: Literal['increase', 'decrease']

def load_config() -> dict:
    """Load configuration from config.yaml."""
    config_path = PROJECT_ROOT / 'config.yaml'
    try:
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return {}

def detect_anomalies(engine: sa.Engine, kpi_name: str, config: dict) -> List[Anomaly]:
    """
    Detect anomalies in a KPI time series using STL decomposition and robust z-scores.
    """
    anomaly_config = config.get('anomaly_detection', {})
    min_data_points = anomaly_config.get('min_data_points', 30)
    stl_period = anomaly_config.get('stl_period', 7)
    z_score_threshold = anomaly_config.get('z_score_threshold', 3)
    
    query = f"SELECT order_date, {kpi_name} FROM kpi_daily ORDER BY order_date"
    try:
        df = pd.read_sql(query, engine)
    except Exception as e:
        logger.error(f"Error querying KPI {kpi_name}: {e}")
        return []
        
    df['order_date'] = pd.to_datetime(df['order_date']).dt.date
    df.set_index('order_date', inplace=True)
    df.dropna(subset=[kpi_name], inplace=True)
    
    if len(df) < min_data_points:
        logger.warning(f"Not enough data for {kpi_name}. Found {len(df)}, need {min_data_points}.")
        return []
        
    try:
        stl = STL(df[kpi_name], period=stl_period, robust=True)
        result = stl.fit()
    except Exception as e:
        logger.error(f"STL decomposition failed for {kpi_name}: {e}")
        return []
        
    residuals = result.resid
    expected = result.trend + result.seasonal
    
    # Robust z-score
    median_resid = np.median(residuals)
    mad_resid = np.median(np.abs(residuals - median_resid))
    
    if mad_resid == 0:
        logger.warning(f"MAD is zero for {kpi_name}, cannot compute robust z-score.")
        return []
        
    # Constant for Normal distribution consistency
    z_scores = 0.6745 * (residuals - median_resid) / mad_resid
    
    anomalies = []
    for dt, z, act, exp in zip(df.index, z_scores, df[kpi_name], expected):
        if abs(z) > z_score_threshold:
            pct_change = ((act - exp) / exp) * 100 if exp != 0 else 0
            direction = 'increase' if act > exp else 'decrease'
            anomalies.append(Anomaly(
                kpi=kpi_name,
                date=dt,
                actual=float(act),
                expected=float(exp),
                pct_change=float(pct_change),
                z_score=float(z),
                direction=direction
            ))
            
    return anomalies

def detect_anomalies_wow(engine: sa.Engine, kpi_name: str, config: dict) -> List[Anomaly]:
    """
    Detect anomalies based on Week-over-Week percentage change.
    """
    anomaly_config = config.get('anomaly_detection', {})
    wow_pct_threshold = anomaly_config.get('wow_pct_threshold', 20)
    
    query = f"SELECT order_date, {kpi_name} FROM kpi_daily ORDER BY order_date"
    try:
        df = pd.read_sql(query, engine)
    except Exception as e:
        logger.error(f"Error querying KPI {kpi_name}: {e}")
        return []
        
    df['order_date'] = pd.to_datetime(df['order_date']).dt.date
    df.set_index('order_date', inplace=True)
    df.dropna(subset=[kpi_name], inplace=True)
    
    # Compare with 7 days ago
    df['expected'] = df[kpi_name].shift(7)
    
    anomalies = []
    for dt, row in df.iterrows():
        act = row[kpi_name]
        exp = row['expected']
        
        if pd.isna(exp) or exp == 0:
            continue
            
        pct_change = ((act - exp) / exp) * 100
        
        if abs(pct_change) > wow_pct_threshold:
            direction = 'increase' if act > exp else 'decrease'
            anomalies.append(Anomaly(
                kpi=kpi_name,
                date=dt,
                actual=float(act),
                expected=float(exp),
                pct_change=float(pct_change),
                z_score=0.0, # N/A for WoW
                direction=direction
            ))
            
    return anomalies

def detect_all_anomalies(engine: sa.Engine, config: dict) -> Dict[str, List[Anomaly]]:
    """
    Run detection for all KPIs defined in config.
    """
    kpis = config.get('kpis', {}).keys()
    results = {}
    for kpi in kpis:
        logger.info(f"Detecting anomalies for {kpi}...")
        anoms = detect_anomalies(engine, kpi, config)
        results[kpi] = anoms
    return results

def plot_kpi_with_anomalies(engine: sa.Engine, kpi_name: str, anomalies: List[Anomaly]) -> go.Figure:
    """
    Plotly line chart of the KPI over time with red markers on anomaly dates.
    """
    query = f"SELECT order_date, {kpi_name} FROM kpi_daily ORDER BY order_date"
    df = pd.read_sql(query, engine)
    df['order_date'] = pd.to_datetime(df['order_date']).dt.date
    
    fig = go.Figure()
    
    # Base line chart
    fig.add_trace(go.Scatter(
        x=df['order_date'],
        y=df[kpi_name],
        mode='lines',
        name=kpi_name,
        line=dict(color='blue')
    ))
    
    # Anomaly markers
    if anomalies:
        anomaly_dates = [a.date for a in anomalies]
        anomaly_values = [a.actual for a in anomalies]
        
        fig.add_trace(go.Scatter(
            x=anomaly_dates,
            y=anomaly_values,
            mode='markers',
            name='Anomaly',
            marker=dict(color='red', size=10, symbol='x')
        ))
        
    fig.update_layout(
        title=f"Anomaly Detection: {kpi_name}",
        xaxis_title="Date",
        yaxis_title=kpi_name,
        template="plotly_white"
    )
    
    return fig

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    config = load_config()
    
    db_path = PROJECT_ROOT / 'data' / 'metric.duckdb'
    engine = sa.create_engine(f'duckdb:///{db_path}')
    
    results = detect_all_anomalies(engine, config)
    for kpi, anoms in results.items():
        print(f"{kpi}: {len(anoms)} anomalies detected.")
