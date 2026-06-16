import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from app.services.data_service import DashboardDataService
from app.services.dashboard_sql_client import DashboardSqlClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print the Databricks and SQL modem-health data that feeds the dashboard join."
    )
    parser.add_argument(
        "--account",
        action="append",
        default=[],
        help="Specific subscriber account number to inspect. Repeat for multiple accounts.",
    )
    parser.add_argument("--location", default="", help="Optional billing city filter when deriving accounts from truckroll data.")
    parser.add_argument("--limit", type=int, default=5, help="Number of truckroll accounts to sample when --account is not provided.")
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output for easier manual inspection.",
    )
    return parser.parse_args()


def _dump(title: str, payload, pretty: bool) -> None:
    print(title)
    if pretty:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(json.dumps(payload, default=str))
    print()


def main() -> int:
    args = parse_args()
    app = create_app()
    service = DashboardDataService(app.config)
    sql_client = DashboardSqlClient(app.config)

    if args.account:
        account_numbers = [service._normalize_account_number(account) for account in args.account if account]
        truckroll_rows = []
    else:
        truckroll_rows = service.client.fetch_truckroll_rows(args.location, args.limit)
        account_numbers = [
            service._normalize_account_number(row[1])
            for row in truckroll_rows
            if len(row) > 1 and row[1] is not None
        ]

    account_to_mac = sql_client.fetch_account_mac_map(account_numbers)

    mapping_rows = [
        {"account_number": account_number, "modem_mac": modem_mac}
        for account_number, modem_mac in account_to_mac.items()
    ]

    modem_rows = sql_client.fetch_latest_modem_health(list(account_to_mac.values())) if account_to_mac else {}
    joined_rows = []
    for account_number in account_numbers:
        modem_mac = account_to_mac.get(account_number, "")
        joined_rows.append(
            {
                "account_number": account_number,
                "modem_mac": modem_mac,
                "sql_row_found": modem_mac in modem_rows,
                "sql_row": modem_rows.get(modem_mac, {}),
            }
        )

    _dump(
        "Configuration",
        {
            "data_source_mode": app.config.get("DATA_SOURCE_MODE"),
            "location": args.location or "ALL LOCATIONS",
            "limit": args.limit,
            "sql_server": app.config.get("DASHBOARD_SQL_SERVER"),
            "sql_database": app.config.get("DASHBOARD_SQL_DATABASE"),
        },
        args.pretty,
    )
    _dump("Truckroll sample", truckroll_rows[: args.limit], args.pretty)
    _dump("Account sample", account_numbers, args.pretty)
    _dump("Dashboard account-to-MAC mapping", mapping_rows, args.pretty)
    _dump("SQL modem rows", modem_rows, args.pretty)
    _dump("Joined node health rows", joined_rows, args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())