# Data Quality Report

**Generated on:** 2026-09-23 12:29:06

## Summary
- **Total Tables Loaded:** 9
- **Total Rows Loaded:** 1289091

## Table Details

### olist_orders_dataset
- **Rows Loaded:** 99441
- **Duplicates Dropped:** 0
- **Null Percentages (columns > 0%):**
  - order_approved_at: 0.16%
  - order_delivered_carrier_date: 1.79%
  - order_delivered_customer_date: 2.98%
- **Date Ranges:**
  - order_purchase_timestamp: 2016-09-04T21:15:19 to 2018-10-17T17:30:18
  - order_approved_at: 2016-09-15T12:16:38 to 2018-09-03T17:40:06
  - order_delivered_carrier_date: 2016-10-08T10:34:01 to 2018-09-11T19:48:28
  - order_delivered_customer_date: 2016-10-11T13:46:32 to 2018-10-17T13:22:46
  - order_estimated_delivery_date: 2016-09-30T00:00:00 to 2018-11-12T00:00:00

### olist_order_items_dataset
- **Rows Loaded:** 112650
- **Duplicates Dropped:** 0
- **Null Percentages:** All columns 0%
- **Date Ranges:**
  - shipping_limit_date: 2016-09-19T00:15:34 to 2020-04-09T22:35:08

### olist_order_payments_dataset
- **Rows Loaded:** 103886
- **Duplicates Dropped:** 0
- **Null Percentages:** All columns 0%

### olist_order_reviews_dataset
- **Rows Loaded:** 99224
- **Duplicates Dropped:** 0
- **Null Percentages (columns > 0%):**
  - review_comment_title: 88.34%
  - review_comment_message: 58.7%
- **Date Ranges:**
  - review_creation_date: 2016-10-02T00:00:00 to 2018-08-31T00:00:00
  - review_answer_timestamp: 2016-10-07T18:32:28 to 2018-10-29T12:27:35

### olist_customers_dataset
- **Rows Loaded:** 99441
- **Duplicates Dropped:** 0
- **Null Percentages:** All columns 0%

### olist_sellers_dataset
- **Rows Loaded:** 3095
- **Duplicates Dropped:** 0
- **Null Percentages:** All columns 0%

### olist_products_dataset
- **Rows Loaded:** 32951
- **Duplicates Dropped:** 0
- **Null Percentages (columns > 0%):**
  - product_category_name: 1.85%
  - product_name_lenght: 1.85%
  - product_description_lenght: 1.85%
  - product_photos_qty: 1.85%
  - product_weight_g: 0.01%
  - product_length_cm: 0.01%
  - product_height_cm: 0.01%
  - product_width_cm: 0.01%

### olist_geolocation_dataset
- **Rows Loaded:** 738332
- **Duplicates Dropped:** 261831
- **Null Percentages:** All columns 0%

### product_category_name_translation
- **Rows Loaded:** 71
- **Duplicates Dropped:** 0
- **Null Percentages:** All columns 0%

