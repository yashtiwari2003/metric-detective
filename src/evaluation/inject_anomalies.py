import os
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
import random
from sqlalchemy import text
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / '.env')
logger = logging.getLogger(__name__)

def inject_anomalies(engine, config: dict) -> list[dict]:
    """
    Injects 30 known anomalies into a copy of fact_orders for evaluation purposes.
    Returns a list of ground truth dictionaries.
    """
    logger.info("Starting anomaly injection...")
    
    # Create evaluation table copy
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS fact_orders_eval"))
        conn.execute(text("CREATE TABLE fact_orders_eval AS SELECT * FROM fact_orders"))
        
    ground_truths = []
    
    # We will simulate 30 anomalies between 2017-06-01 and 2018-06-01
    start_date = datetime(2017, 6, 1)
    date_pool = [start_date + timedelta(days=x) for x in range(365)]
    
    # Define states, categories, payment types for injection
    states = ['SP', 'RJ', 'MG', 'RS', 'PR']
    categories = ['bed_bath_table', 'health_beauty', 'sports_leisure', 'furniture_decor', 'computers_accessories']
    payment_types = ['credit_card', 'boleto', 'voucher', 'debit_card']
    
    anomaly_id_counter = 1
    
    def get_random_dates(duration_days=5):
        start = random.choice(date_pool[:-duration_days])
        end = start + timedelta(days=duration_days)
        return start, end

    with engine.begin() as conn:
        # a. 5x Region drop
        for i in range(5):
            s, e = get_random_dates(5)
            state = random.choice(states)
            # Delete 40% of orders for state
            query = f"""
                DELETE FROM fact_orders_eval 
                WHERE customer_state = '{state}' 
                AND order_date >= '{s.strftime('%Y-%m-%d')}' 
                AND order_date < '{e.strftime('%Y-%m-%d')}'
                AND random() < 0.4
            """
            conn.execute(text(query))
            ground_truths.append({
                "anomaly_id": anomaly_id_counter,
                "type": "region_drop",
                "kpi_affected": "orders",
                "start_date": s.strftime('%Y-%m-%d'),
                "end_date": e.strftime('%Y-%m-%d'),
                "root_cause_dimension": "customer_state",
                "root_cause_segment": state,
                "expected_pct_change": -0.40
            })
            anomaly_id_counter += 1
            
        # b. 5x Category collapse
        for i in range(5):
            s, e = get_random_dates(3)
            category = random.choice(categories)
            query = f"""
                DELETE FROM fact_orders_eval 
                WHERE category = '{category}' 
                AND order_date >= '{s.strftime('%Y-%m-%d')}' 
                AND order_date < '{e.strftime('%Y-%m-%d')}'
            """
            conn.execute(text(query))
            ground_truths.append({
                "anomaly_id": anomaly_id_counter,
                "type": "category_collapse",
                "kpi_affected": "orders",
                "start_date": s.strftime('%Y-%m-%d'),
                "end_date": e.strftime('%Y-%m-%d'),
                "root_cause_dimension": "category",
                "root_cause_segment": category,
                "expected_pct_change": -1.00
            })
            anomaly_id_counter += 1
            
        # c. 3x Payment failure
        for i in range(3):
            s, e = get_random_dates(4)
            payment_type = random.choice(payment_types)
            query = f"""
                DELETE FROM fact_orders_eval 
                WHERE payment_type = '{payment_type}' 
                AND order_date >= '{s.strftime('%Y-%m-%d')}' 
                AND order_date < '{e.strftime('%Y-%m-%d')}'
            """
            conn.execute(text(query))
            ground_truths.append({
                "anomaly_id": anomaly_id_counter,
                "type": "payment_failure",
                "kpi_affected": "orders",
                "start_date": s.strftime('%Y-%m-%d'),
                "end_date": e.strftime('%Y-%m-%d'),
                "root_cause_dimension": "payment_type",
                "root_cause_segment": payment_type,
                "expected_pct_change": -1.00
            })
            anomaly_id_counter += 1
            
        # d. 4x Delivery delay spike
        for i in range(4):
            s, e = get_random_dates(7)
            state = random.choice(states)
            query = f"""
                UPDATE fact_orders_eval 
                SET delivery_days = delivery_days + 10, is_late = 1
                WHERE seller_state = '{state}' 
                AND order_date >= '{s.strftime('%Y-%m-%d')}' 
                AND order_date < '{e.strftime('%Y-%m-%d')}'
            """
            conn.execute(text(query))
            ground_truths.append({
                "anomaly_id": anomaly_id_counter,
                "type": "delivery_delay",
                "kpi_affected": "late_delivery_rate",
                "start_date": s.strftime('%Y-%m-%d'),
                "end_date": e.strftime('%Y-%m-%d'),
                "root_cause_dimension": "seller_state",
                "root_cause_segment": state,
                "expected_pct_change": None
            })
            anomaly_id_counter += 1
            
        # e. 3x Review score drop
        for i in range(3):
            s, e = get_random_dates(5)
            query = f"""
                UPDATE fact_orders_eval 
                SET review_score = GREATEST(1, review_score - 2)
                WHERE order_date >= '{s.strftime('%Y-%m-%d')}' 
                AND order_date < '{e.strftime('%Y-%m-%d')}'
            """
            conn.execute(text(query))
            ground_truths.append({
                "anomaly_id": anomaly_id_counter,
                "type": "review_score_drop",
                "kpi_affected": "avg_review_score",
                "start_date": s.strftime('%Y-%m-%d'),
                "end_date": e.strftime('%Y-%m-%d'),
                "root_cause_dimension": "overall",
                "root_cause_segment": "overall",
                "expected_pct_change": None
            })
            anomaly_id_counter += 1
            
        # f. 3x Price change
        for i in range(3):
            s, e = get_random_dates(5)
            query = f"""
                UPDATE fact_orders_eval 
                SET price = price * 0.7
                WHERE order_date >= '{s.strftime('%Y-%m-%d')}' 
                AND order_date < '{e.strftime('%Y-%m-%d')}'
            """
            conn.execute(text(query))
            ground_truths.append({
                "anomaly_id": anomaly_id_counter,
                "type": "price_change",
                "kpi_affected": "revenue",
                "start_date": s.strftime('%Y-%m-%d'),
                "end_date": e.strftime('%Y-%m-%d'),
                "root_cause_dimension": "overall",
                "root_cause_segment": "overall",
                "expected_pct_change": -0.30
            })
            anomaly_id_counter += 1
            
        # g. 2x Mixed/two-cause
        for i in range(2):
            s, e = get_random_dates(5)
            state = random.choice(states)
            category = random.choice(categories)
            query = f"""
                DELETE FROM fact_orders_eval 
                WHERE customer_state = '{state}' 
                AND category = '{category}'
                AND order_date >= '{s.strftime('%Y-%m-%d')}' 
                AND order_date < '{e.strftime('%Y-%m-%d')}'
            """
            conn.execute(text(query))
            ground_truths.append({
                "anomaly_id": anomaly_id_counter,
                "type": "mixed_drop",
                "kpi_affected": "orders",
                "start_date": s.strftime('%Y-%m-%d'),
                "end_date": e.strftime('%Y-%m-%d'),
                "root_cause_dimension": "customer_state,category",
                "root_cause_segment": f"{state},{category}",
                "expected_pct_change": -1.00
            })
            anomaly_id_counter += 1
            
        # h. 5x Noise decoys
        for i in range(5):
            s, e = get_random_dates(3)
            query = f"""
                DELETE FROM fact_orders_eval 
                WHERE order_date >= '{s.strftime('%Y-%m-%d')}' 
                AND order_date < '{e.strftime('%Y-%m-%d')}'
                AND random() < 0.05
            """
            conn.execute(text(query))
            ground_truths.append({
                "anomaly_id": anomaly_id_counter,
                "type": "noise_decoy",
                "kpi_affected": "orders",
                "start_date": s.strftime('%Y-%m-%d'),
                "end_date": e.strftime('%Y-%m-%d'),
                "root_cause_dimension": "none",
                "root_cause_segment": "none",
                "expected_pct_change": -0.05
            })
            anomaly_id_counter += 1

    # Save to disk
    eval_dir = PROJECT_ROOT / 'evaluation'
    eval_dir.mkdir(exist_ok=True, parents=True)
    with open(eval_dir / 'ground_truth.json', 'w') as f:
        json.dump(ground_truths, f, indent=2)
        
    logger.info(f"Injected {len(ground_truths)} anomalies and saved ground truth.")
    return ground_truths

if __name__ == "__main__":
    from sqlalchemy import create_engine
    import yaml
    
    with open(PROJECT_ROOT / 'config.yaml', 'r') as f:
        config = yaml.safe_load(f)
        
    engine = create_engine(f"duckdb:///{PROJECT_ROOT}/data/metric.duckdb")
    inject_anomalies(engine, config)
