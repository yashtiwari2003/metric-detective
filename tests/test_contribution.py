"""
Tests for contribution analysis module.
Verifies that segment contributions sum to ~100% for additive KPIs.
"""
import pytest
import yaml
from pathlib import Path
from sqlalchemy import create_engine
from src.analysis.contribution import compute_contributions

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_additive_contributions_sum_to_100():
    """Verify that dimension contributions sum to approximately 100% of total change."""
    config_path = PROJECT_ROOT / "config.yaml"
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    # Use single dimension without top_n limit to ensure complete partition
    test_config = {
        "kpis": config["kpis"],
        "contribution_analysis": config["contribution_analysis"],
        "dimensions": [
            {"name": "payment_type", "display_name": "Payment Type", "top_n": None}
        ]
    }

    db_path = PROJECT_ROOT / "data" / "metric.duckdb"
    engine = create_engine(f"duckdb:///{db_path}")

    # Use a 7-day period in 2018
    result = compute_contributions(engine, "revenue", "2018-05-01", "2018-05-07", test_config)
    
    assert result.total_delta != 0, "Total delta should not be zero"
    assert len(result.segments) > 0, "Expected segment contributions"

    # Filter to payment_type dimension segments
    pt_segments = [s for s in result.segments if s.dimension == "payment_type"]
    total_contrib = sum(s.contribution_pct for s in pt_segments)

    # Sum should be very close to 100% (e.g., within 2% allowing for any null payment types)
    assert 98.0 <= total_contrib <= 102.0, f"Expected ~100% sum of contributions, got {total_contrib:.2f}%"
