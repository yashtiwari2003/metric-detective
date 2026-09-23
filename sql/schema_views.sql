-- Drop the table if it already exists to allow rerunning
DROP TABLE IF EXISTS fact_orders;

-- Create fact_orders table
CREATE TABLE fact_orders AS
WITH
-- 1. Aggregate payments to get dominant payment type and total payment value per order
order_payments_agg AS (
    SELECT 
        order_id,
        -- arg_max returns the payment_type associated with the max payment_value
        arg_max(payment_type, payment_value) AS dominant_payment_type,
        SUM(payment_value) AS total_payment_value
    FROM olist_order_payments_dataset
    GROUP BY order_id
),
-- 2. Aggregate reviews to get max review score per order (in case of multiple reviews)
order_reviews_agg AS (
    SELECT 
        order_id,
        MAX(review_score) AS review_score
    FROM olist_order_reviews_dataset
    GROUP BY order_id
)
-- Main selection joining all necessary tables
SELECT 
    -- Identifiers for traceability
    o.order_id,
    oi.order_item_id,
    c.customer_id,
    oi.seller_id,
    oi.product_id,

    -- Temporal attributes and derived dates
    CAST(o.order_purchase_timestamp AS DATE) AS order_date,
    
    -- Delivery calculations
    -- DATE_DIFF in DuckDB calculates the difference in 'day's. NULL if not delivered (dates are null).
    DATE_DIFF('day', CAST(o.order_purchase_timestamp AS TIMESTAMP), CAST(o.order_delivered_customer_date AS TIMESTAMP)) AS delivery_days,
    -- Check if delivery was late. NULL if not delivered.
    CASE 
        WHEN o.order_delivered_customer_date IS NULL THEN NULL
        WHEN CAST(o.order_delivered_customer_date AS TIMESTAMP) > CAST(o.order_estimated_delivery_date AS TIMESTAMP) THEN 1 
        ELSE 0 
    END AS is_late,

    -- Order status flags
    CASE 
        WHEN o.order_status = 'canceled' THEN 1 
        ELSE 0 
    END AS is_canceled,

    -- Financial metrics
    oi.price,
    oi.freight_value,
    op.dominant_payment_type AS payment_type,

    -- Review score (max per order)
    orv.review_score,

    -- Customer & Seller Geography
    c.customer_state,
    s.seller_state,

    -- Product category translation (English or fallback to original)
    COALESCE(t.product_category_name_english, p.product_category_name) AS category

FROM olist_orders_dataset o
-- LEFT JOIN items as fact_orders is at the order_item level (one order can have multiple items)
LEFT JOIN olist_order_items_dataset oi 
    ON o.order_id = oi.order_id
-- LEFT JOIN customers to get customer geography (state)
LEFT JOIN olist_customers_dataset c 
    ON o.customer_id = c.customer_id
-- LEFT JOIN sellers to get seller geography (state)
LEFT JOIN olist_sellers_dataset s 
    ON oi.seller_id = s.seller_id
-- LEFT JOIN products to get the original product category name
LEFT JOIN olist_products_dataset p 
    ON oi.product_id = p.product_id
-- LEFT JOIN translation to get the English category name
LEFT JOIN product_category_name_translation t 
    ON p.product_category_name = t.product_category_name
-- LEFT JOIN payment aggregation to get dominant payment type
LEFT JOIN order_payments_agg op 
    ON o.order_id = op.order_id
-- LEFT JOIN review aggregation to get the max review score
LEFT JOIN order_reviews_agg orv 
    ON o.order_id = orv.order_id;
