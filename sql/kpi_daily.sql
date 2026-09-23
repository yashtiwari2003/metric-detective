-- Create kpi_daily table using DuckDB syntax
DROP TABLE IF EXISTS kpi_daily;
CREATE TABLE kpi_daily AS
SELECT
    order_date,
    -- Total revenue (price + freight)
    SUM(price + freight_value) AS revenue,
    -- Total distinct orders
    COUNT(DISTINCT order_id) AS orders,
    -- Average Order Value
    SUM(price + freight_value) / NULLIF(COUNT(DISTINCT order_id), 0) AS avg_order_value,
    -- Average delivery days
    AVG(delivery_days) AS avg_delivery_days,
    -- Late delivery rate
    AVG(CAST(is_late AS FLOAT)) AS late_delivery_rate,
    -- Cancellation rate
    AVG(CAST(is_canceled AS FLOAT)) AS cancellation_rate,
    -- Average review score
    AVG(review_score) AS avg_review_score
FROM fact_orders
WHERE order_date IS NOT NULL
GROUP BY order_date
ORDER BY order_date;
