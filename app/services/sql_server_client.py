import logging
from typing import Any


logger = logging.getLogger(__name__)


class SqlServerClient:
    def __init__(self, config: dict[str, Any]):
        self.server = config.get("MODEM_SQL_SERVER", "")
        self.port = str(config.get("MODEM_SQL_PORT", "1433"))
        self.database = config.get("MODEM_SQL_DATABASE", "")
        self.username = config.get("MODEM_SQL_USERNAME", "")
        self.password = config.get("MODEM_SQL_PASSWORD", "")
        self.driver = config.get("MODEM_SQL_DRIVER", "ODBC Driver 17 for SQL Server")
        self.schema = config.get("MODEM_SQL_SCHEMA", "dbo")
        self.table = config.get("MODEM_SQL_TABLE", "cmdata2011")
        self.encrypt = config.get("MODEM_SQL_ENCRYPT", "yes")
        self.trust_server_certificate = config.get("MODEM_SQL_TRUST_SERVER_CERTIFICATE", "yes")

    def is_configured(self) -> bool:
        return all([self.server, self.database, self.username, self.password, self.driver])

    def fetch_latest_modem_health(self, mac_addresses: list[str]) -> dict[str, dict[str, Any]]:
        sanitized_macs = []
        seen_macs = set()
        for mac_address in mac_addresses:
            normalized_mac = self.normalize_mac_key(mac_address)
            if not normalized_mac or normalized_mac in seen_macs:
                continue
            seen_macs.add(normalized_mac)
            sanitized_macs.append(normalized_mac)

        if not sanitized_macs:
            return {}
        if not self.is_configured():
            logger.info("Skipping modem health query because SQL Server is not configured.")
            return {}

        placeholders = ", ".join("?" for _ in sanitized_macs)
        query = f"""
WITH ranked_modems AS (
    SELECT
        *,
                UPPER(REPLACE(LTRIM(RTRIM(CONVERT(VARCHAR(255), mac))), ':', '')) AS normalized_mac_key,
                ROW_NUMBER() OVER (
                        PARTITION BY UPPER(REPLACE(LTRIM(RTRIM(CONVERT(VARCHAR(255), mac))), ':', ''))
                        ORDER BY tstamp DESC
                ) AS row_rank
    FROM newbacondata.{self.schema}.{self.table}
    WHERE ip <> '0.0.0.0'
            AND UPPER(REPLACE(LTRIM(RTRIM(CONVERT(VARCHAR(255), mac))), ':', '')) IN ({placeholders})
)
SELECT *
FROM ranked_modems
WHERE row_rank = 1
ORDER BY tstamp DESC
""".strip()

        connection = None
        cursor = None
        try:
            pyodbc = __import__("pyodbc")
            connection = pyodbc.connect(self._build_connection_string(), timeout=30)
            cursor = connection.cursor()
            cursor.execute(query, sanitized_macs)
            column_names = [column[0] for column in cursor.description]
            rows = cursor.fetchall()
            modem_rows: dict[str, dict[str, Any]] = {}
            for row in rows:
                row_dict = {
                    column_name: self._serialize_value(value)
                    for column_name, value in zip(column_names, row, strict=False)
                }
                modem_key = self.normalize_mac_key(row_dict.get("normalized_mac_key") or row_dict.get("mac"))
                if modem_key:
                    modem_rows[modem_key] = row_dict
            logger.info("Fetched modem health rows from SQL Server. row_count=%s", len(modem_rows))
            return modem_rows
        except ModuleNotFoundError as exc:
            logger.warning("pyodbc is not installed; modem health integration is unavailable.")
            raise RuntimeError("pyodbc is required for modem health integration.") from exc
        except Exception:
            logger.exception("Failed to load modem health rows from SQL Server.")
            raise
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

    def _build_connection_string(self) -> str:
        server_value = self.server
        if self.port and str(self.port).strip() not in {"", "1433"}:
            server_value = f"{self.server},{self.port}"
        return (
            f"DRIVER={{{self.driver}}};"
            f"SERVER={server_value};"
            f"DATABASE={self.database};"
            f"UID={self.username};"
            f"PWD={self.password};"
        )

    @staticmethod
    def normalize_mac_key(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip().replace(":", "").upper()

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        if hasattr(value, "isoformat"):
            return value.isoformat(sep=" ", timespec="seconds")
        return value