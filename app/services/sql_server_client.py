import logging
from typing import Any


logger = logging.getLogger(__name__)


class SqlServerClient:
    def __init__(self, config: dict[str, Any]):
        self.server = config.get("MODEM_SQL_SERVER", "")
        self.port = str(config.get("MODEM_SQL_PORT", "1433"))
        self.timeout_seconds = int(config.get("MODEM_SQL_TIMEOUT_SECONDS", 5))
        self.batch_size = int(config.get("MODEM_SQL_BATCH_SIZE", 500))
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

        connection = None
        cursor = None
        try:
            pyodbc = __import__("pyodbc")
            connection = pyodbc.connect(self._build_connection_string(), timeout=self.timeout_seconds)
            cursor = connection.cursor()
            modem_rows: dict[str, dict[str, Any]] = {}
            for batch in self._iter_batches(sanitized_macs):
                query = self._build_modem_health_query(batch)
                cursor.execute(query, *batch)
                column_names = [column[0] for column in cursor.description]
                rows = cursor.fetchall()
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
        encrypt_value = "yes" if str(self.encrypt).strip().lower() in {"yes", "true", "1"} else "no"
        trust_server_certificate_value = (
            "yes" if str(self.trust_server_certificate).strip().lower() in {"yes", "true", "1"} else "no"
        )
        return (
            f"DRIVER={{{self.driver}}};"
            f"SERVER={server_value};"
            f"DATABASE={self.database};"
            f"UID={self.username};"
            f"PWD={self.password};"
            f"Encrypt={encrypt_value};"
            f"TrustServerCertificate={trust_server_certificate_value};"
        )

    def _build_modem_health_query(self, mac_addresses: list[str]) -> str:
        placeholders = ", ".join("?" for _ in mac_addresses)
        return f"""
WITH ranked_modems AS (
    SELECT
        *,
                UPPER(REPLACE(LTRIM(RTRIM(CONVERT(VARCHAR(255), mac))), ':', '')) AS normalized_mac_key,
                ROW_NUMBER() OVER (
                        PARTITION BY UPPER(REPLACE(LTRIM(RTRIM(CONVERT(VARCHAR(255), mac))), ':', ''))
                        ORDER BY tstamp DESC
                ) AS row_rank
    FROM {self.database}.{self.schema}.{self.table}
    WHERE ip <> '0.0.0.0'
            AND UPPER(REPLACE(LTRIM(RTRIM(CONVERT(VARCHAR(255), mac))), ':', '')) IN ({placeholders})
)
SELECT *
FROM ranked_modems
WHERE row_rank = 1
ORDER BY tstamp DESC
""".strip()

    def _iter_batches(self, items: list[str]) -> list[list[str]]:
        batch_size = max(self.batch_size, 1)
        return [items[index:index + batch_size] for index in range(0, len(items), batch_size)]

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