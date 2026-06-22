import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    DATA_SOURCE_MODE = os.getenv("DATA_SOURCE_MODE", "mock")
    REFRESH_SECONDS = int(os.getenv("REFRESH_SECONDS", "60"))
    HIGH_RISK_LIMIT = int(os.getenv("HIGH_RISK_LIMIT", "12"))
    MAX_DASHBOARD_LIMIT = int(os.getenv("MAX_DASHBOARD_LIMIT", "10000"))
    LOG_DIR = os.getenv("LOG_DIR", str(Path(__file__).resolve().parent / "logs"))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "14"))

    DATABRICKS_HOST = os.getenv("DATABRICKS_HOST", "")
    DATABRICKS_WAREHOUSE_ID = os.getenv("DATABRICKS_WAREHOUSE_ID", "")
    DATABRICKS_TOKEN = os.getenv("DATABRICKS_TOKEN", "")
    DATABRICKS_SQL_QUERY = os.getenv("DATABRICKS_SQL_QUERY", "")
    DATABRICKS_SQL_QUERY_FILE = os.getenv("DATABRICKS_SQL_QUERY_FILE", "")

    DASHBOARD_SQL_SERVER = os.getenv("DASHBOARD_SQL_SERVER", os.getenv("TARGET_SQL_SERVER", ""))
    DASHBOARD_SQL_PORT = int(os.getenv("DASHBOARD_SQL_PORT", "1433"))
    DASHBOARD_SQL_TIMEOUT_SECONDS = int(os.getenv("DASHBOARD_SQL_TIMEOUT_SECONDS", "15"))
    DASHBOARD_SQL_BATCH_SIZE = int(os.getenv("DASHBOARD_SQL_BATCH_SIZE", "500"))
    DASHBOARD_SQL_DATABASE = os.getenv("DASHBOARD_SQL_DATABASE", os.getenv("TARGET_SQL_DATABASE", ""))
    DASHBOARD_SQL_USERNAME = os.getenv("DASHBOARD_SQL_USERNAME", os.getenv("TARGET_SQL_USERNAME", ""))
    DASHBOARD_SQL_PASSWORD = os.getenv("DASHBOARD_SQL_PASSWORD", os.getenv("TARGET_SQL_PASSWORD", ""))
    DASHBOARD_SQL_DRIVER = os.getenv(
        "DASHBOARD_SQL_DRIVER",
        os.getenv("TARGET_SQL_DRIVER", "ODBC Driver 17 for SQL Server"),
    )
    DASHBOARD_SQL_SCHEMA = os.getenv("DASHBOARD_SQL_SCHEMA", "dbo")
    DASHBOARD_SQL_ENCRYPT = os.getenv("DASHBOARD_SQL_ENCRYPT", os.getenv("TARGET_SQL_ENCRYPT", "yes"))
    DASHBOARD_SQL_TRUST_SERVER_CERTIFICATE = os.getenv(
        "DASHBOARD_SQL_TRUST_SERVER_CERTIFICATE",
        os.getenv("TARGET_SQL_TRUST_SERVER_CERTIFICATE", "yes"),
    )
    DASHBOARD_SQL_TRUCKROLL_TABLE = os.getenv("DASHBOARD_SQL_TRUCKROLL_TABLE", "service_churn_truckroll_base")
    DASHBOARD_SQL_RES_CHURN_TABLE = os.getenv("DASHBOARD_SQL_RES_CHURN_TABLE", "service_churn_res_latest")
    DASHBOARD_SQL_COM_CHURN_TABLE = os.getenv("DASHBOARD_SQL_COM_CHURN_TABLE", "service_churn_com_latest")
    DASHBOARD_SQL_CALL_MONTHLY_TABLE = os.getenv(
        "DASHBOARD_SQL_CALL_MONTHLY_TABLE",
        "service_churn_call_monthly_agg",
    )
    DASHBOARD_SQL_CALL_RECORDS_TABLE = os.getenv(
        "DASHBOARD_SQL_CALL_RECORDS_TABLE",
        "service_churn_call_records_monthly",
    )
    DASHBOARD_SQL_ACCOUNT_MAC_TABLE = os.getenv("DASHBOARD_SQL_ACCOUNT_MAC_TABLE", "service_churn_account_mac_map")
    DASHBOARD_SQL_MODEM_HEALTH_TABLE = os.getenv(
        "DASHBOARD_SQL_MODEM_HEALTH_TABLE",
        "service_churn_modem_health_latest",
    )

    MODEM_HEALTH_REFRESH_SECONDS = int(os.getenv("MODEM_HEALTH_REFRESH_SECONDS", "3600"))
    MODEM_SQL_SERVER = os.getenv("MODEM_SQL_SERVER", "")
    MODEM_SQL_PORT = int(os.getenv("MODEM_SQL_PORT", "1433"))
    MODEM_SQL_TIMEOUT_SECONDS = int(os.getenv("MODEM_SQL_TIMEOUT_SECONDS", "5"))
    MODEM_SQL_DATABASE = os.getenv("MODEM_SQL_DATABASE", "newbacondata")
    MODEM_SQL_USERNAME = os.getenv("MODEM_SQL_USERNAME", "")
    MODEM_SQL_PASSWORD = os.getenv("MODEM_SQL_PASSWORD", "")
    MODEM_SQL_DRIVER = os.getenv("MODEM_SQL_DRIVER", "ODBC Driver 18 for SQL Server")
    MODEM_SQL_SCHEMA = os.getenv("MODEM_SQL_SCHEMA", "dbo")
    MODEM_SQL_TABLE = os.getenv("MODEM_SQL_TABLE", "cmdata2011")
    MODEM_SQL_ENCRYPT = os.getenv("MODEM_SQL_ENCRYPT", "yes")
    MODEM_SQL_TRUST_SERVER_CERTIFICATE = os.getenv("MODEM_SQL_TRUST_SERVER_CERTIFICATE", "yes")
