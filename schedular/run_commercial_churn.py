import os

os.environ["RUN_ONLY_TABLES"] = "dbo.service_churn_com_latest"
os.environ["STATE_SCOPE"] = "commercial_churn"

from dashboard_schedular import refresh_once


if __name__ == "__main__":
    refresh_once()