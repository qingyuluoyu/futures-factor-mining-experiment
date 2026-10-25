import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.international_metals import MAX_BYTES, audit_international_metals


def read_bounded(path: str) -> bytes:
    with Path(path).open("rb") as source:
        payload = source.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("缓存超过2MiB边界")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="国际贵金属离线审计；仅读取缓存并输出审计报告，不调用上游、不写数据库")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--catalog")
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--collected-at", type=datetime.fromisoformat)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        report = audit_international_metals(read_bounded(args.payload), symbol=args.symbol, start_date=args.start,
            end_date=args.end, as_of=datetime.now(timezone.utc), collected_at=args.collected_at,
            catalog_payload=read_bounded(args.catalog) if args.catalog else None)
        destination = Path(args.output).resolve()
        serialized = json.dumps(report, ensure_ascii=True, allow_nan=False, indent=2)
        with destination.open("x", encoding="utf-8") as target:
            target.write(serialized)
        summary = {field: report[field] for field in ("audit_version", "instrument_id", "validation_status",
            "numerical_validation", "source_row_count", "validated_row_count", "capabilities")}
        summary.update(issue_codes=[issue["code"] for issue in report["issues"]], report_path=str(destination))
        print(json.dumps(summary, ensure_ascii=True))
        return 3 if report["validation_status"] == "rejected" else 2
    except Exception as error:
        print(f"International metals audit failed ({type(error).__name__}); source content omitted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
