# Power BI Dashboard Documentation

This directory contains resources for the Metric Detective Power BI Dashboard.

## 1. DAX Measures

You can create the following measures in your Power BI model:

```dax
Revenue = SUMX(fact_orders, [price] + [freight_value])

Orders = DISTINCTCOUNT(fact_orders[order_id])

AOV = [Revenue] / [Orders]

Late Delivery % = DIVIDE(CALCULATE(COUNTROWS(fact_orders), fact_orders[is_late]=1), COUNTROWS(fact_orders))

WoW Revenue % = ([Revenue] - CALCULATE([Revenue], DATEADD(kpi_daily[order_date], -7, DAY))) / CALCULATE([Revenue], DATEADD(kpi_daily[order_date], -7, DAY))

Anomaly Flag = IF(ABS([WoW Revenue %]) > 0.2, 1, 0)
```

## 2. Suggested 2-Page Layout

**Page 1: KPI Overview**
- **Cards**: Orders, Revenue, AOV, Late Delivery %
- **Line Charts**: Daily Revenue vs. 7-Day Moving Average
- **Slicers**: Date range, State, Category

**Page 2: Deep Dive**
- **Bar Charts**: Revenue by Category, Orders by Customer State
- **Timeline**: Anomaly detection timeline highlighting dates where `Anomaly Flag = 1`

## 3. Data Model Relationships

Connect `fact_orders` and `kpi_daily`:
- Link `fact_orders[order_date]` to `kpi_daily[order_date]` (Many-to-One)

## 4. Instructions for Importing CSVs

1. Open Power BI Desktop.
2. Click **Get Data** -> **Text/CSV**.
3. Navigate to `data/fact_orders.csv` and load.
4. Repeat for `data/kpi_daily.csv`.
5. Go to the **Model View** to create the relationships described above.
6. Create a New Measure for each of the DAX formulas provided.
