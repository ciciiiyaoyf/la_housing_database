"""Load the saved SQL selection into pandas without changing the database."""

from pathlib import Path
import sys

import duckdb
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "database" / "housing.duckdb"
SQL_PATH = PROJECT_ROOT / "sql" / "model_input.sql"


def load_model_data(
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> pd.DataFrame:
    """Return the selected rows, retaining nulls, dates, and original values."""
    path = Path(database_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")
    query = SQL_PATH.read_text(encoding="utf-8")
    with duckdb.connect(str(path), read_only=True) as connection:
        return connection.sql(query).df()


def main() -> int:
    try:
        frame = load_model_data()
    except (FileNotFoundError, duckdb.Error) as error:
        print(f"Could not load model inputs: {error}", file=sys.stderr)
        return 1
    print(f"Rows: {len(frame):,}")
    if not frame.empty:
        print(f"Dates: {frame['date'].min().date()} through {frame['date'].max().date()}")
    print("Null values by column:")
    print(frame.isna().sum().to_string())
    print("First 10 rows:")
    print(frame.head(10).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
