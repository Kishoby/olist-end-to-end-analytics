"""run_pipeline.py - run the whole project in order.

  python run_pipeline.py              SQL Server: Python ETL -> sql/02_star_schema.sql -> analysis -> features -> model -> validation
  python run_pipeline.py --no-sql     no SQL Server: same, but the star schema is built by 00_build_star_sqlite.py
                                      (set OLIST_DB_URL to e.g. sqlite:///olist.db)
After a SQL Server run, also execute sql/03, 04 and 05 in SSMS (analysis queries, performance objects, ML views).
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SQL_DIR = HERE.parent / "sql"


def run_py(script):
    print(f"\n=== {script} " + "=" * (60 - len(script)))
    subprocess.run([sys.executable, str(HERE / script)], check=True, cwd=HERE)


def run_sql_file(path):
    from config import get_engine
    print(f"\n=== {path.name} " + "=" * (60 - len(path.name)))
    batches = [b for b in re.split(r"^\s*GO\s*$", path.read_text(), flags=re.M | re.I) if b.strip()]
    with get_engine().connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for b in batches:
            conn.exec_driver_sql(b)


if __name__ == "__main__":
    no_sql = "--no-sql" in sys.argv
    run_py("01_etl.py")
    if no_sql:
        run_py("00_build_star_sqlite.py")
    else:
        sys.path.insert(0, str(HERE))
        run_sql_file(SQL_DIR / "02_star_schema.sql")
    for s in ["02_analysis.py", "03_feature_engineering.py", "04_train_model.py", "05_validate.py"]:
        run_py(s)
    print("\nPipeline finished.")
