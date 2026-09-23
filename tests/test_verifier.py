"""
Tests for the AI verifier module.
Verifies that the verifier catches hallucinated/unmatched numbers in reports.
"""
import pytest
import pandas as pd
from src.ai.verifier import Evidence, verify_report_numbers


def test_verifier_catches_wrong_number():
    """Verify that a hallucinated number not present in evidence is flagged."""
    df = pd.DataFrame({
        "category": ["bed_bath_table", "health_beauty"],
        "orders": [120, 85],
        "revenue": [15420.50, 9300.00]
    })
    
    evidence = Evidence(
        hypothesis_id="H1",
        query_id="Q1",
        sql="SELECT category, count(*), sum(price) FROM fact_orders GROUP BY category",
        result_df_json=df.to_json(orient='records'),
        verdict="SUPPORTED"
    )
    
    # 1. Report with accurate numbers only
    accurate_report = "Revenue for bed_bath_table reached 15420.50 with 120 orders, while health_beauty had 85 orders."
    is_valid, unmatched = verify_report_numbers(accurate_report, [evidence], tolerance_pct=1.0)
    assert is_valid, f"Accurate report was wrongly flagged with unmatched: {unmatched}"
    assert len(unmatched) == 0

    # 2. Report with a hallucinated number (e.g., 999999 or 45000.0)
    hallucinated_report = "Revenue reached 999999 with 85 orders."
    is_valid, unmatched = verify_report_numbers(hallucinated_report, [evidence], tolerance_pct=1.0)
    assert not is_valid, "Verifier should have caught the hallucinated number!"
    assert any("999999" in u for u in unmatched)
