import os

os.environ["RUN_ONLY_TABLES"] = "dbo.service_churn_truckroll_base"
os.environ["STATE_SCOPE"] = "truckroll"

from dashboard_schedular import refresh_once


if __name__ == "__main__":
    refresh_once()