import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from app.services.data_service import DashboardDataService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check live churn enrichment for the dashboard watchlist.")
    parser.add_argument("--location", default="", help="Optional billing city filter.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of truckroll accounts to inspect.")
    parser.add_argument(
        "--fail-on-zero-match",
        action="store_true",
        help="Exit with a non-zero status code when no churn rows match the truckroll watchlist.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app = create_app()
    service = DashboardDataService(app.config)
    limit = args.limit or app.config.get("HIGH_RISK_LIMIT", 12)

    truckroll_rows = service.client.fetch_truckroll_rows(args.location, limit)
    truckroll_accounts = [service._normalize_account_number(row[1]) for row in truckroll_rows if len(row) > 1 and row[1] is not None]

    churn_rows = service.client.fetch_churn_rows(truckroll_accounts, "res")
    matched_accounts = {service._normalize_account_number(row[0]) for row in churn_rows if len(row) > 0 and row[0] is not None}
    unmatched_accounts = [account for account in truckroll_accounts if account not in matched_accounts]

    snapshot = service.get_dashboard_snapshot(location=args.location, limit=limit)

    print("Configuration")
    print(json.dumps({
        "data_source_mode": app.config.get("DATA_SOURCE_MODE"),
        "location": args.location or "ALL LOCATIONS",
        "limit": limit,
    }, indent=2))

    print("Counts")
    print(json.dumps({
        "truckroll_rows": len(truckroll_rows),
        "churn_rows": len(churn_rows),
        "matched_accounts": len(matched_accounts),
        "unmatched_accounts": len(unmatched_accounts),
        "signal_mix_items": len(snapshot.get("signal_mix", [])),
    }, indent=2))

    print("Matched churn sample")
    print(json.dumps(churn_rows[:5], indent=2))

    print("Unmatched account sample")
    print(json.dumps(unmatched_accounts[:10], indent=2))

    print("Dashboard KPI snapshot")
    print(json.dumps(snapshot.get("kpis", []), indent=2))

    print("Dashboard customer sample")
    print(json.dumps(snapshot.get("high_risk_customers", [])[:5], indent=2))

    if args.fail_on_zero_match and not matched_accounts:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())