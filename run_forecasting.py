"""Run the Week 1 historical forecasting exercises on the local housing database."""

import argparse
from pathlib import Path

from src.forecasting.week1 import run_week1


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=root / "database/housing.duckdb")
    parser.add_argument("--config", type=Path, default=root / "config/forecasting_week1.yaml")
    parser.add_argument("--output", type=Path, default=root / "output/forecasting/week1")
    args = parser.parse_args()
    run_week1(args.database, args.config, args.output)
    print(f"Week 1 forecasts and memo: {args.output.resolve()}")
