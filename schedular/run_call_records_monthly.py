import os

os.environ["RUN_ONLY_TABLES"] = "dbo.service_churn_call_records_monthly"
os.environ["STATE_SCOPE"] = "call_records_monthly"

from dashboard_schedular import refresh_once


if __name__ == "__main__":
    refresh_once()