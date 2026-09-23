"""
Tests for anomaly detection module.
Verifies that the anomaly detector accurately identifies a planted spike.
"""
import pytest
import pandas as pd
import numpy as np
from datetime import date, timedelta
from sqlalchemy import create_engine
from src.detection.anomaly import detect_anomalies


def test_detect_planted_spike():
    """Verify that STL + robust z-score flags a planted anomaly spike."""
    engine = create_engine("duckdb:///:memory:")
    
    start_date = date(2023, 1, 1)
    num_days = 90
    dates = [start_date + timedelta(days=i) for i in range(num_days)]
    
    # Baseline with mild weekly pattern + noise
    np.random.seed(42)
    values = [100.0 + 10.0 * np.sin(2 * np.pi * (i % 7) / 7.0) + np.random.normal(0, 2) for i in range(num_days)]
    
    # Plant a massive spike on day 45
    spike_idx = 45
    spike_date = dates[spike_idx]
    values[spike_idx] = 350.0  # >10 stddev spike
    
    df = pd.DataFrame({
        "order_date": dates,
        "revenue": values
    })
    df.to_sql("kpi_daily", engine, index=False)
    
    config = {
        "anomaly_detection": {
            "min_data_points": 30,
            "stl_period": 7,
            "z_score_threshold": 3.0
        }
    }
    
    anomalies = detect_anomalies(engine, "revenue", config)
    assert len(anomalies) > 0, "Expected at least one anomaly to be detected."
    
    anomaly_dates = [a.date for a in anomalies]
    assert spike_date in anomaly_dates, f"Planted spike on {spike_date} was not detected!"
    
    spike_anomaly = next(a for a in anomalies if a.date == spike_date)
    assert spike_anomaly.direction == "increase"
    assert spike_anomaly.z_score > 3.0
