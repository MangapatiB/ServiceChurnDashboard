import os

os.environ["RUN_ONLY_TABLES"] = "dbo.service_churn_res_latest"
os.environ["STATE_SCOPE"] = "residential_churn"

from dashboard_schedular import refresh_once


if __name__ == "__main__":
    refresh_once()