"""
Data Loading Script for Metric Detective.

Loads Olist CSVs from data/raw/ into a DuckDB database.
"""

import os
import sys
import logging
from pathlib import Path
from datetime import datetime
import pandas as pd
import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, Engine

# CSV to Table mapping
CSV_TABLE_MAP = {
    'olist_orders_dataset.csv': 'olist_orders_dataset',
    'olist_order_items_dataset.csv': 'olist_order_items_dataset',
    'olist_order_payments_dataset.csv': 'olist_order_payments_dataset',
    'olist_order_reviews_dataset.csv': 'olist_order_reviews_dataset',
    'olist_customers_dataset.csv': 'olist_customers_dataset',
    'olist_sellers_dataset.csv': 'olist_sellers_dataset',
    'olist_products_dataset.csv': 'olist_products_dataset',
    'olist_geolocation_dataset.csv': 'olist_geolocation_dataset',
    'product_category_name_translation.csv': 'product_category_name_translation',
}

# Date columns for parsing
DATE_COLUMNS = {
    'olist_orders_dataset': [
        'order_purchase_timestamp',
        'order_approved_at',
        'order_delivered_carrier_date',
        'order_delivered_customer_date',
        'order_estimated_delivery_date'
    ],
    'olist_order_items_dataset': [
        'shipping_limit_date'
    ],
    'olist_order_reviews_dataset': [
        'review_creation_date',
        'review_answer_timestamp'
    ]
}

logger = logging.getLogger(__name__)

def get_engine(db_url: str, project_root: Path) -> Engine:
    """
    Create a SQLAlchemy engine.
    
    Args:
        db_url (str): Database URL.
        project_root (Path): Root directory of the project.
        
    Returns:
        Engine: SQLAlchemy Engine instance.
    """
    if db_url.startswith('duckdb:///'):
        db_path = db_url.replace('duckdb:///', '')
        db_full_path = project_root / db_path
        # Ensure parent directory exists
        db_full_path.parent.mkdir(parents=True, exist_ok=True)
        # Update db_url to use absolute path to avoid issues
        db_url = f"duckdb:///{db_full_path}"
        
    logger.info(f"Creating database engine for {db_url}")
    return create_engine(db_url)

def load_csv(filepath: Path, table_name: str, engine: Engine, date_columns: list[str] | None = None) -> dict:
    """
    Load a CSV file into the database.
    
    Args:
        filepath (Path): Path to the CSV file.
        table_name (str): Name of the table to create/replace.
        engine (Engine): SQLAlchemy engine.
        date_columns (list[str] | None): List of date columns to parse.
        
    Returns:
        dict: Statistics about the loaded data.
    """
    logger.info(f"Loading {filepath.name} into table {table_name}")
    
    # Read CSV
    df = pd.read_csv(filepath)
    original_row_count = len(df)
    
    # Parse dates
    date_range = {}
    if date_columns:
        for col in date_columns:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce')
                # Calculate date range for valid dates
                valid_dates = df[col].dropna()
                if not valid_dates.empty:
                    date_range[col] = {
                        'min': valid_dates.min().isoformat(),
                        'max': valid_dates.max().isoformat()
                    }
                else:
                    date_range[col] = {'min': None, 'max': None}
                    
    # Drop duplicates
    df = df.drop_duplicates()
    dropped_dupes_count = original_row_count - len(df)
    
    # Standardize text columns
    for col in df.select_dtypes(include=['object']):
        df[col] = df[col].astype(str).str.strip()
        # Specific cleaning for products table
        if table_name == 'olist_products_dataset' and col == 'product_category_name':
            df[col] = df[col].str.lower()
            
    # Calculate null percentages
    null_pct = (df.isnull().sum() / len(df) * 100).round(2).to_dict() if len(df) > 0 else {}
    null_pct_filtered = {k: v for k, v in null_pct.items() if v > 0}
    
    # Write to database
    df.to_sql(table_name, engine, if_exists='replace', index=False)
    
    return {
        'table_name': table_name,
        'rows_loaded': len(df),
        'rows_dropped_dupes': dropped_dupes_count,
        'null_pct_by_column': null_pct_filtered,
        'date_range': date_range
    }

def generate_data_quality_report(all_stats: list[dict], output_path: Path) -> None:
    """
    Generate a markdown report with data quality statistics.
    
    Args:
        all_stats (list[dict]): List of dictionaries containing table stats.
        output_path (Path): Path to save the markdown report.
    """
    logger.info(f"Generating data quality report at {output_path}")
    
    total_tables = len(all_stats)
    total_rows = sum(stat['rows_loaded'] for stat in all_stats)
    
    # Ensure directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("# Data Quality Report\n\n")
        f.write(f"**Generated on:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        f.write("## Summary\n")
        f.write(f"- **Total Tables Loaded:** {total_tables}\n")
        f.write(f"- **Total Rows Loaded:** {total_rows}\n\n")
        
        f.write("## Table Details\n\n")
        for stat in all_stats:
            f.write(f"### {stat['table_name']}\n")
            f.write(f"- **Rows Loaded:** {stat['rows_loaded']}\n")
            f.write(f"- **Duplicates Dropped:** {stat['rows_dropped_dupes']}\n")
            
            if stat['null_pct_by_column']:
                f.write("- **Null Percentages (columns > 0%):**\n")
                for col, pct in stat['null_pct_by_column'].items():
                    f.write(f"  - {col}: {pct}%\n")
            else:
                f.write("- **Null Percentages:** All columns 0%\n")
                
            if stat['date_range']:
                f.write("- **Date Ranges:**\n")
                for col, drange in stat['date_range'].items():
                    if drange['min'] and drange['max']:
                        f.write(f"  - {col}: {drange['min']} to {drange['max']}\n")
                    else:
                        f.write(f"  - {col}: No valid dates\n")
            f.write("\n")

def main() -> None:
    """Main execution function."""
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        stream=sys.stdout
    )
    
    # Determine project root and load env
    project_root = Path(__file__).resolve().parents[2]
    env_path = project_root / '.env'
    load_dotenv(env_path)
    
    db_url = os.environ.get('DB_URL', 'duckdb:///data/metric.duckdb')
    
    raw_data_dir = project_root / 'data' / 'raw'
    reports_dir = project_root / 'reports'
    
    # Validate CSVs exist
    missing_files = []
    for csv_file in CSV_TABLE_MAP.keys():
        if not (raw_data_dir / csv_file).exists():
            missing_files.append(csv_file)
            
    if missing_files:
        logger.error(f"Missing CSV files in {raw_data_dir}: {missing_files}")
        sys.exit(1)
        
    engine = get_engine(db_url, project_root)
    
    all_stats = []
    for csv_file, table_name in CSV_TABLE_MAP.items():
        filepath = raw_data_dir / csv_file
        date_columns = DATE_COLUMNS.get(table_name)
        stats = load_csv(filepath, table_name, engine, date_columns)
        all_stats.append(stats)
        
    report_path = reports_dir / 'data_quality.md'
    generate_data_quality_report(all_stats, report_path)
    
    logger.info("Data loading process completed successfully.")

if __name__ == '__main__':
    main()
