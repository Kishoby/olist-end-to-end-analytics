"""Shared settings. Override with environment variables instead of editing code.

  OLIST_DATA_DIR  folder holding the 9 Olist CSV files   (default C:\\olist)
  OLIST_DB_URL    SQLAlchemy URL of the SQL Server DB    (default: local SQL Server, Windows login)
  OLIST_LOAD_REVIEW_TEXT=1  also load review comment text (default 0: not used in this project)
"""
import os
from pathlib import Path
from sqlalchemy import create_engine

DATA_DIR = Path(os.environ.get("OLIST_DATA_DIR", r"C:\olist"))
DB_URL = os.environ.get(
    "OLIST_DB_URL",
    "mssql+pyodbc://@localhost/OlistDB?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes",
)
LOAD_REVIEW_TEXT = os.environ.get("OLIST_LOAD_REVIEW_TEXT", "0") == "1"   # review comments are not needed for this project
PROJECT_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_DIR / "outputs"
MODEL_DIR = PROJECT_DIR / "models"
OUT_DIR.mkdir(exist_ok=True)
MODEL_DIR.mkdir(exist_ok=True)

# Row counts of the original Kaggle files (used to catch a bad or partial download)
EXPECTED_ROWS = {
    "olist_customers_dataset.csv": 99441,
    "olist_orders_dataset.csv": 99441,
    "olist_order_items_dataset.csv": 112650,
    "olist_order_payments_dataset.csv": 103886,
    "olist_order_reviews_dataset.csv": 99224,
    "olist_products_dataset.csv": 32951,
    "olist_sellers_dataset.csv": 3095,
    "product_category_name_translation.csv": 71,
    "olist_geolocation_dataset.csv": 1000163,
}
EXPECTED_ITEM_REVENUE = 13591643.70   # SUM(price) over order items

# Time-based split for the model (by purchase date)
TRAIN_END = "2018-03-01"   # train: purchases before this date
VALID_END = "2018-05-01"   # valid: TRAIN_END <= purchase < VALID_END ; test: on/after VALID_END


def get_engine():
    if DB_URL.startswith("mssql"):
        return create_engine(DB_URL, fast_executemany=True)
    return create_engine(DB_URL)
