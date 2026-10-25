import argparse
import asyncio
from datetime import date
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from scripts.stock_eod_operator import execute_once


def main():
    parser = argparse.ArgumentParser(description="Audited single-day EOD update: at most 3 source calls, no retries or forced recovery")
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--operator-ref", default="local-console")
    arguments = parser.parse_args()
    try:
        result = asyncio.run(execute_once(arguments.date, arguments.output, arguments.operator_ref))
        print(json.dumps({"status": result["status"], "trade_date": result["trade_date"], "source_calls": result["source_calls"], "source_budget": 3, "model_calls": 0, "receipt": arguments.output.name, "database_result": result.get("database_result_observed")}, ensure_ascii=True))
        return 0
    except Exception as error:
        print("Single-day EOD operation stopped (" + type(error).__name__ + "); no automatic retry. Database may already contain the date; verify the receipt and stored batch before any recovery. Credentials and provider details omitted.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
