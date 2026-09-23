import os
import logging
import math
from pathlib import Path
from typing import List, Dict, Any, Optional

from pydantic import BaseModel
import scipy.stats as stats
import numpy as np
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / '.env')
logger = logging.getLogger(__name__)

class TestResult(BaseModel):
    """Results of a statistical test."""
    test_name: str
    statistic: float
    p_value: float
    effect_size: float
    ci_lower: float
    ci_upper: float
    significant: bool
    interpretation: str

def welch_ttest(baseline: List[float], anomaly: List[float], alpha: float = 0.05) -> TestResult:
    """
    Perform a Welch's t-test for independent samples with unequal variances.
    Used for additive or continuous metrics.
    """
    if not baseline or not anomaly:
        return TestResult(
            test_name="Welch's t-test", statistic=0.0, p_value=1.0, effect_size=0.0,
            ci_lower=0.0, ci_upper=0.0, significant=False,
            interpretation="Insufficient data to perform test."
        )

    b_arr = np.array(baseline)
    a_arr = np.array(anomaly)
    
    if np.var(b_arr) == 0 and np.var(a_arr) == 0:
        return TestResult(
            test_name="Welch's t-test", statistic=0.0, p_value=1.0, effect_size=0.0,
            ci_lower=0.0, ci_upper=0.0, significant=False,
            interpretation="No variance in data; test cannot be performed."
        )

    t_stat, p_val = stats.ttest_ind(a_arr, b_arr, equal_var=False)
    
    # Cohen's d effect size
    mean_diff = np.mean(a_arr) - np.mean(b_arr)
    pooled_sd = np.sqrt((np.var(a_arr, ddof=1) + np.var(b_arr, ddof=1)) / 2)
    effect_size = mean_diff / pooled_sd if pooled_sd > 0 else 0.0

    # 95% CI
    df = len(a_arr) + len(b_arr) - 2
    if df > 0:
        ci = stats.t.interval(1 - alpha, df, loc=mean_diff, scale=pooled_sd * np.sqrt(1/len(a_arr) + 1/len(b_arr)))
    else:
        ci = (0.0, 0.0)

    significant = p_val < alpha
    dir_str = "increased" if mean_diff > 0 else "decreased"
    sig_str = "significantly" if significant else "not significantly"
    
    interp = f"The mean {dir_str} {sig_str} from {np.mean(b_arr):.2f} to {np.mean(a_arr):.2f} (p={p_val:.4f}, Cohen's d={effect_size:.2f})."

    return TestResult(
        test_name="Welch's t-test",
        statistic=float(t_stat),
        p_value=float(p_val),
        effect_size=float(effect_size),
        ci_lower=float(ci[0]),
        ci_upper=float(ci[1]),
        significant=bool(significant),
        interpretation=interp
    )

def proportion_ztest(baseline_successes: int, baseline_total: int, anomaly_successes: int, anomaly_total: int, alpha: float = 0.05) -> TestResult:
    """
    Two-proportion z-test. Used for ratio/proportion metrics.
    """
    if baseline_total == 0 or anomaly_total == 0:
        return TestResult(
            test_name="Two-proportion z-test", statistic=0.0, p_value=1.0, effect_size=0.0,
            ci_lower=0.0, ci_upper=0.0, significant=False,
            interpretation="Insufficient data to perform test."
        )

    p1 = baseline_successes / baseline_total
    p2 = anomaly_successes / anomaly_total
    p_pool = (baseline_successes + anomaly_successes) / (baseline_total + anomaly_total)
    
    if p_pool == 0 or p_pool == 1:
        return TestResult(
            test_name="Two-proportion z-test", statistic=0.0, p_value=1.0, effect_size=0.0,
            ci_lower=0.0, ci_upper=0.0, significant=False,
            interpretation="No variance in proportions; test cannot be performed."
        )

    se = np.sqrt(p_pool * (1 - p_pool) * (1/baseline_total + 1/anomaly_total))
    z_stat = (p2 - p1) / se
    p_val = 2 * (1 - stats.norm.cdf(abs(z_stat)))
    
    effect_size = p2 - p1
    z_crit = stats.norm.ppf(1 - alpha/2)
    margin = z_crit * np.sqrt(p1*(1-p1)/baseline_total + p2*(1-p2)/anomaly_total)
    
    ci_lower = effect_size - margin
    ci_upper = effect_size + margin
    significant = p_val < alpha
    
    dir_str = "increased" if effect_size > 0 else "decreased"
    sig_str = "significantly" if significant else "not significantly"
    
    interp = f"The proportion {dir_str} {sig_str} from {p1:.2%} to {p2:.2%} (p={p_val:.4f}, difference={effect_size:.2%})."

    return TestResult(
        test_name="Two-proportion z-test",
        statistic=float(z_stat),
        p_value=float(p_val),
        effect_size=float(effect_size),
        ci_lower=float(ci_lower),
        ci_upper=float(ci_upper),
        significant=bool(significant),
        interpretation=interp
    )

def chi_square_test(baseline_counts: Dict[str, int], anomaly_counts: Dict[str, int], alpha: float = 0.05) -> TestResult:
    """
    Chi-square test of independence for categorical distributions.
    """
    keys = list(set(baseline_counts.keys()).union(set(anomaly_counts.keys())))
    if not keys:
        return TestResult(
            test_name="Chi-square test", statistic=0.0, p_value=1.0, effect_size=0.0,
            ci_lower=0.0, ci_upper=0.0, significant=False,
            interpretation="Insufficient data to perform test."
        )

    b_vals = [baseline_counts.get(k, 0) for k in keys]
    a_vals = [anomaly_counts.get(k, 0) for k in keys]
    
    obs = np.array([b_vals, a_vals])
    
    if np.sum(obs) == 0 or np.any(np.sum(obs, axis=1) == 0):
        return TestResult(
            test_name="Chi-square test", statistic=0.0, p_value=1.0, effect_size=0.0,
            ci_lower=0.0, ci_upper=0.0, significant=False,
            interpretation="Zero counts in data; test cannot be performed."
        )

    chi2, p_val, dof, _ = stats.chi2_contingency(obs)
    
    n = np.sum(obs)
    min_dim = min(obs.shape) - 1
    cramers_v = np.sqrt(chi2 / (n * min_dim)) if min_dim > 0 and n > 0 else 0.0
    
    significant = p_val < alpha
    sig_str = "Significant" if significant else "No significant"
    
    interp = f"{sig_str} difference in categorical distribution (p={p_val:.4f}, Cramér's V={cramers_v:.2f})."

    return TestResult(
        test_name="Chi-square test",
        statistic=float(chi2),
        p_value=float(p_val),
        effect_size=float(cramers_v),
        ci_lower=0.0,
        ci_upper=0.0,
        significant=bool(significant),
        interpretation=interp
    )

def select_and_run_test(kpi_type: str, baseline_data: Any, anomaly_data: Any, alpha: float = 0.05) -> TestResult:
    """
    Automatically select and run the appropriate test based on KPI type.
    """
    try:
        if kpi_type == 'additive':
            return welch_ttest(baseline_data, anomaly_data, alpha)
        elif kpi_type == 'ratio':
            # Assumes data is (successes, total)
            b_succ, b_tot = baseline_data
            a_succ, a_tot = anomaly_data
            return proportion_ztest(b_succ, b_tot, a_succ, a_tot, alpha)
        elif kpi_type == 'categorical':
            return chi_square_test(baseline_data, anomaly_data, alpha)
        else:
            raise ValueError(f"Unknown KPI type: {kpi_type}")
    except Exception as e:
        logger.error(f"Error running stats test: {e}")
        return TestResult(
            test_name="Error", statistic=0.0, p_value=1.0, effect_size=0.0,
            ci_lower=0.0, ci_upper=0.0, significant=False,
            interpretation=f"Error executing test: {e}"
        )
