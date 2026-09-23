# Sample Investigation Report: Revenue Anomaly

**Metric:** Revenue  
**Anomaly Period:** 2018-05-01 to 2018-05-07  
**Investigation Mode:** AI Multi-Agent with SQL Guard and Verifier  

---

## Headline
An anomaly in revenue occurred on 2018-05-07 with an actual value of 43,762.63 against an expected value of 25,720.05, representing a 70.15% increase [Anomaly Object].

## What Happened
- **KPI:** revenue [Anomaly Object]
- **Anomaly Date/Period:** 2018-05-07 [Anomaly Object]
- **Actual Value:** $43,762.63 [Anomaly Object]
- **Expected Value:** $25,720.05 [Anomaly Object]
- **Percentage Change:** +70.15% increase [Anomaly Object]
- **Statistical Severity:** z-score of 3.50 [Anomaly Object]

## Root Causes (Ranked by Contribution %)
1. **Regional Seller Distribution and Revenue Concentration (State SP):** Segment name SP contributing to overall totals with 80,342 total orders and 8,753,396.21 total revenue [Query ID: Q2].
2. **Seller Order Volume and Delivery Metrics:** Supported hypothesis examining top sellers such as `6560211a19b47992c3666cc44a7e94c0` with 2,033 total orders and a late_rate of 0.062 [Query ID: Q4].
3. **Payment Type Distribution Changes:** Supported hypothesis showing significant shifts in payment types across order months (e.g., voucher orders and monthly counts) [Query ID: Q1, Query ID: Q6].

## Evidence Details
- **Query ID: Q1:** Evaluated voucher payment types and order dates using Welch's t-test, which yielded a statistic of 2.15, a p-value of 0.035, and an effect size of 0.48 (interpreting that the mean increased significantly from 4,039.84 to 8,567.43 with confidence interval bounds 323.50 and 8,731.69).
- **Query ID: Q2:** Analyzed seller states (such as SP with 1,849 unique sellers, 80,342 total orders, 8,753,396.21 total revenue, and 108.95 avg item price) using a Two-proportion z-test that resulted in a statistic of 9.82, p-value of 0.000, and effect size of 0.017 (with ci_lower 0.013 and ci_upper 0.021).
- **Query ID: Q4:** Tested seller order volumes and delivery metrics, yielding a Welch's t-test statistic of -6.32, p-value of 0.000017, and effect size of -2.83 (ci_lower -978.37, ci_upper -490.23), showing a significant decrease in the mean from 1,594.20 to 859.90.
- **Query ID: Q6:** Examined monthly order counts by payment type using a Chi-square test, producing a statistic of 3,153.00, p-value of 0.000, and effect size of 1.00.

## Rejected Hypotheses
- **H3 (Query ID: Q3):** The hypothesis testing the watches_gifts category average item price over time was rejected (Welch's t-test statistic = -2.24, p_value = 0.051, not statistically significant at alpha = 0.05).
- **H5 (Query ID: Q5):** The hypothesis testing customer state delivery days and cancellation rates was rejected/inconclusive due to insufficient data for testing (Two-proportion z-test statistic = 0.0, p_value = 1.0).

## Recommended Actions
- Monitor regional sales concentrations in high-revenue states like SP [Query ID: Q2] to ensure logistics infrastructure can handle demand surges.
- Investigate top-performing sellers (such as seller ID `6560211a19b47992c3666cc44a7e94c0`) to replicate their order fulfillment success [Query ID: Q4].
- Track shifts in payment types (e.g., credit card, boleto, and voucher trends) to optimize checkout experiences and promotional campaigns [Query ID: Q1, Query ID: Q6].

## Confidence Level
**High**  
Justification: The anomaly has a high statistical severity (z-score of 3.50) [Anomaly Object], and multiple underlying hypotheses (H1, H2, H4, H6) yielded statistically significant test results with strong p-values (p < 0.05) across robust sample sizes [Query ID: Q1, Query ID: Q2, Query ID: Q4, Query ID: Q6].
