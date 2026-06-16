import os

os.environ["RUN_ONLY_TABLES"] = "dbo.service_churn_account_mac_map"
os.environ["STATE_SCOPE"] = "account_mac"

from dashboard_schedular import refresh_once


if __name__ == "__main__":
    refresh_once()