"""
Data Download Script for Metric Detective.

Downloads the Brazilian E-Commerce (Olist) dataset from Kaggle using the Kaggle CLI
and validates the presence of all required CSV files.
"""

import logging
import os
from pathlib import Path
import subprocess
import sys

from dotenv import load_dotenv

# Project root directory: parent of the scripts directory
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Load environment variables from .env located at the project root
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

# Set up module logger
logger = logging.getLogger(__name__)

# Expected 9 CSV files in the Olist dataset
EXPECTED_FILES: list[str] = [
    "olist_orders_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_order_payments_dataset.csv",
    "olist_order_reviews_dataset.csv",
    "olist_customers_dataset.csv",
    "olist_sellers_dataset.csv",
    "olist_products_dataset.csv",
    "olist_geolocation_dataset.csv",
    "product_category_name_translation.csv",
]

# Kaggle dataset slug
KAGGLE_DATASET = "olistbr/brazilian-ecommerce"


def download_dataset(raw_dir: Path) -> None:
    """
    Download and unzip the Olist dataset from Kaggle into the target directory.

    Authenticates using the KAGGLE_KEY environment variable if present.
    Executes the Kaggle CLI command via subprocess.

    Args:
        raw_dir: Destination Path where dataset CSV files will be extracted.

    Raises:
        subprocess.CalledProcessError: If the Kaggle CLI command fails.
        FileNotFoundError: If the Kaggle executable cannot be located.
    """
    # Ensure destination directory exists
    raw_dir.mkdir(parents=True, exist_ok=True)

    # Read and configure KAGGLE_KEY for Kaggle CLI authentication
    kaggle_key = os.getenv("KAGGLE_KEY")
    if kaggle_key:
        os.environ["KAGGLE_KEY"] = kaggle_key
        logger.info("KAGGLE_KEY found in environment.")
    else:
        logger.warning(
            "KAGGLE_KEY not found in environment. "
            "Kaggle CLI will rely on existing credentials if available."
        )

    # Use the kaggle executable from the same Python environment
    # (handles venv where 'kaggle' may not be on the system PATH)
    kaggle_exe = Path(sys.executable).parent / "kaggle"
    if sys.platform == "win32":
        kaggle_exe = kaggle_exe.with_suffix(".exe")

    cmd = [
        str(kaggle_exe),
        "datasets",
        "download",
        "-d",
        KAGGLE_DATASET,
        "-p",
        str(raw_dir),
        "--unzip",
    ]

    logger.info("Starting dataset download from Kaggle: %s", KAGGLE_DATASET)
    logger.info("Destination directory: %s", raw_dir)

    try:
        subprocess.run(cmd, check=True)
        logger.info("Kaggle dataset download and extraction completed successfully.")
    except subprocess.CalledProcessError as exc:
        logger.error("Kaggle CLI failed with return code %s: %s", exc.returncode, exc)
        raise
    except FileNotFoundError as exc:
        logger.error(
            "Kaggle executable not found. Ensure 'kaggle' is installed in the active environment: %s",
            exc,
        )
        raise


def validate_files(raw_dir: Path) -> bool:
    """
    Check that all 9 expected Olist CSV files exist in the specified directory.

    Args:
        raw_dir: Path to directory containing the dataset files.

    Returns:
        bool: True if all 9 expected files are present, False otherwise.
    """
    if not raw_dir.exists():
        logger.warning("Target directory does not exist: %s", raw_dir)
        return False

    present_files: list[str] = []
    missing_files: list[str] = []

    for filename in EXPECTED_FILES:
        filepath = raw_dir / filename
        if filepath.is_file():
            present_files.append(filename)
            logger.info("File present: %s (%d bytes)", filename, filepath.stat().st_size)
        else:
            missing_files.append(filename)
            logger.warning("File missing: %s", filename)

    if missing_files:
        logger.warning(
            "Validation failed: %d missing files out of %d expected.",
            len(missing_files),
            len(EXPECTED_FILES),
        )
        return False

    logger.info(
        "Validation successful: all %d expected CSV files are present in %s.",
        len(EXPECTED_FILES),
        raw_dir,
    )
    return True


def main() -> None:
    """
    Execute the data download workflow:
    1. Set up logging.
    2. Determine the raw data directory (project_root / 'data' / 'raw').
    3. Check if all 9 CSV files exist; if so, skip download.
    4. Otherwise, download dataset and validate files.
    5. Exit with status code 1 if validation fails.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    raw_dir = PROJECT_ROOT / "data" / "raw"
    logger.info("Raw data directory set to: %s", raw_dir)

    # Check if files are already downloaded and valid
    if validate_files(raw_dir):
        logger.info("All 9 dataset files already exist. Skipping download.")
        return

    logger.info("Files missing or incomplete. Proceeding with download...")
    try:
        download_dataset(raw_dir)
    except Exception as exc:
        logger.error("Failed to download dataset: %s", exc)
        sys.exit(1)

    # Validate extracted files post-download
    if not validate_files(raw_dir):
        logger.error("Dataset validation failed after download.")
        sys.exit(1)

    logger.info("Dataset successfully downloaded and verified.")


if __name__ == "__main__":
    main()
