import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    DATA_SOURCE_MODE = os.getenv("DATA_SOURCE_MODE", "mock")
    REFRESH_SECONDS = int(os.getenv("REFRESH_SECONDS", "60"))
    HIGH_RISK_LIMIT = int(os.getenv("HIGH_RISK_LIMIT", "12"))
    LOG_DIR = os.getenv("LOG_DIR", str(Path(__file__).resolve().parent / "logs"))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "14"))

    DATABRICKS_HOST = os.getenv("DATABRICKS_HOST", "")
    DATABRICKS_WAREHOUSE_ID = os.getenv("DATABRICKS_WAREHOUSE_ID", "")
    DATABRICKS_TOKEN = os.getenv("DATABRICKS_TOKEN", "")
    DATABRICKS_SQL_QUERY = os.getenv("DATABRICKS_SQL_QUERY", "")
    DATABRICKS_SQL_QUERY_FILE = os.getenv("DATABRICKS_SQL_QUERY_FILE", "")

    MODEM_HEALTH_REFRESH_SECONDS = int(os.getenv("MODEM_HEALTH_REFRESH_SECONDS", "3600"))
    MODEM_SQL_SERVER = os.getenv("MODEM_SQL_SERVER", "")
    MODEM_SQL_PORT = int(os.getenv("MODEM_SQL_PORT", "1433"))
    MODEM_SQL_DATABASE = os.getenv("MODEM_SQL_DATABASE", "newbacondata")
    MODEM_SQL_USERNAME = os.getenv("MODEM_SQL_USERNAME", "")
    MODEM_SQL_PASSWORD = os.getenv("MODEM_SQL_PASSWORD", "")
    MODEM_SQL_DRIVER = os.getenv("MODEM_SQL_DRIVER", "ODBC Driver 18 for SQL Server")
    MODEM_SQL_SCHEMA = os.getenv("MODEM_SQL_SCHEMA", "dbo")
    MODEM_SQL_TABLE = os.getenv("MODEM_SQL_TABLE", "cmdata2011")
    MODEM_SQL_ENCRYPT = os.getenv("MODEM_SQL_ENCRYPT", "yes")
    MODEM_SQL_TRUST_SERVER_CERTIFICATE = os.getenv("MODEM_SQL_TRUST_SERVER_CERTIFICATE", "yes")
