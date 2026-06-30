import logging
from typing import Any

from app.services.query_builders import normalize_limit, sanitize_location


logger = logging.getLogger(__name__)


SQL_SERVER_PARAMETER_LIMIT = 2100
DEFAULT_DASHBOARD_SQL_BATCH_SIZE = 2000


class DashboardSqlQuerySession:
    def __init__(self, client: "DashboardSqlClient"):
        self._client = client
        self._connection = None

    def __enter__(self) -> "DashboardSqlQuerySession":
        self._connection = self._client._open_connection()
        return self

    def __exit__(self, exc_type, exc, exc_tb) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def fetch_rows(self, query: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
        cursor = None
        try:
            cursor = self._connection.cursor()
            cursor.execute(query, *(params or []))
            return [tuple(row) for row in cursor.fetchall()]
        finally:
            if cursor is not None:
                cursor.close()

    def fetch_dict_rows(self, query: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        cursor = None
        try:
            cursor = self._connection.cursor()
            cursor.execute(query, *(params or []))
            column_names = [column[0] for column in cursor.description]
            rows = []
            for row in cursor.fetchall():
                rows.append(
                    {
                        column_name: self._client._serialize_value(value)
                        for column_name, value in zip(column_names, row, strict=False)
                    }
                )
            return rows
        finally:
            if cursor is not None:
                cursor.close()


class DashboardSqlClient:
    def __init__(self, config: dict[str, Any]):
        self.server = config.get("DASHBOARD_SQL_SERVER", "")
        self.port = str(config.get("DASHBOARD_SQL_PORT", "1433"))
        self.timeout_seconds = int(config.get("DASHBOARD_SQL_TIMEOUT_SECONDS", 15))
        self.batch_size = int(config.get("DASHBOARD_SQL_BATCH_SIZE", DEFAULT_DASHBOARD_SQL_BATCH_SIZE))
        self.database = config.get("DASHBOARD_SQL_DATABASE", "")
        self.username = config.get("DASHBOARD_SQL_USERNAME", "")
        self.password = config.get("DASHBOARD_SQL_PASSWORD", "")
        self.driver = config.get("DASHBOARD_SQL_DRIVER", "ODBC Driver 17 for SQL Server")
        self.schema = config.get("DASHBOARD_SQL_SCHEMA", "dbo")
        self.encrypt = config.get("DASHBOARD_SQL_ENCRYPT", "yes")
        self.trust_server_certificate = config.get("DASHBOARD_SQL_TRUST_SERVER_CERTIFICATE", "yes")
        self.truckroll_table = config.get("DASHBOARD_SQL_TRUCKROLL_TABLE", "service_churn_truckroll_base")
        self.res_churn_table = config.get("DASHBOARD_SQL_RES_CHURN_TABLE", "service_churn_res_latest")
        self.com_churn_table = config.get("DASHBOARD_SQL_COM_CHURN_TABLE", "service_churn_com_latest")
        self.call_monthly_table = config.get("DASHBOARD_SQL_CALL_MONTHLY_TABLE", "service_churn_call_monthly_agg")
        self.call_records_table = config.get("DASHBOARD_SQL_CALL_RECORDS_TABLE", "service_churn_call_records_monthly")
        self.account_mac_table = config.get("DASHBOARD_SQL_ACCOUNT_MAC_TABLE", "service_churn_account_mac_map")
        self.modem_health_table = config.get("DASHBOARD_SQL_MODEM_HEALTH_TABLE", "service_churn_modem_health_latest")

    def is_configured(self) -> bool:
        return all([self.server, self.database, self.username, self.password, self.driver])

    def open_session(self) -> DashboardSqlQuerySession:
        return DashboardSqlQuerySession(self)

    def fetch_location_options(self, query_session: DashboardSqlQuerySession | None = None) -> list[str]:
        query = (
            f"SELECT DISTINCT UPPER(BillingCity) AS BillingCity "
            f"FROM {self._qualified_table(self.truckroll_table)} "
            "WHERE BillingCity IS NOT NULL "
            "ORDER BY BillingCity"
        )
        rows = self._fetch_rows(query, query_session=query_session)
        return [str(row[0]).strip() for row in rows if row and row[0] is not None]

    def fetch_truckroll_rows(
        self,
        location: str = "",
        limit: int | None = None,
        query_session: DashboardSqlQuerySession | None = None,
    ) -> list[tuple[Any, ...]]:
        safe_location = sanitize_location(location)
        safe_limit = normalize_limit(limit, default=25)
        params: list[Any] = []
        where_clauses = ["SubscriberAccountNumber IS NOT NULL"]
        if safe_location:
            where_clauses.append("UPPER(BillingCity) = ?")
            params.append(safe_location)

        query = (
            f"SELECT TOP {safe_limit} LegacyAccountNumber, SubscriberAccountNumber, PhoneNumber, UPPER(BillingCity) AS BillingCity "
            f"FROM {self._qualified_table(self.truckroll_table)} "
            f"WHERE {' AND '.join(where_clauses)} "
            "ORDER BY "
            "CASE WHEN BillingCity IS NULL OR LTRIM(RTRIM(BillingCity)) = '' THEN 1 ELSE 0 END, "
            "UPPER(BillingCity), SubscriberAccountNumber"
        )
        return self._fetch_rows(query, params, query_session=query_session)

    def fetch_churn_rows(
        self,
        account_numbers: list[str],
        customer_segment: str,
        query_session: DashboardSqlQuerySession | None = None,
    ) -> list[tuple[Any, ...]]:
        sanitized_accounts = self._sanitize_string_keys(account_numbers)
        if not sanitized_accounts:
            return []

        churn_table = self.com_churn_table if customer_segment == "com" else self.res_churn_table
        rows: list[tuple[Any, ...]] = []
        for batch in self._iter_batches(sanitized_accounts):
            placeholders = ", ".join("?" for _ in batch)
            query = (
                "SELECT SubscriberAccountNumber, ChurnProbability, PredictionMonth, Top1Feature, Top2Feature, Top3Feature "
                f"FROM {self._qualified_table(churn_table)} "
                f"WHERE SubscriberAccountNumber IN ({placeholders}) "
                "ORDER BY ChurnProbability DESC"
            )
            rows.extend(self._fetch_rows(query, batch, query_session=query_session))
        return rows

    def fetch_call_monthly_rows(
        self,
        subscriber_account_numbers: list[str],
        customer_segment: str,
        query_session: DashboardSqlQuerySession | None = None,
    ) -> list[tuple[Any, ...]]:
        sanitized_accounts = self._sanitize_string_keys(subscriber_account_numbers)
        if not sanitized_accounts:
            return []

        customer_type_code = "COM" if customer_segment == "com" else "RES"
        rows: list[tuple[Any, ...]] = []
        for batch in self._iter_batches(sanitized_accounts, fixed_params=1):
            placeholders = ", ".join("?" for _ in batch)
            query = (
                "SELECT NumberOfCalls, AccountNumber, MonthStart, ContactMonthStart, "
                "AverageAgentTalkMin, AverageTotalContactDurationMin, TotalAgentTalkMin, TotalContactDurationMin "
                f"FROM {self._qualified_table(self.call_monthly_table)} "
                f"WHERE CustomerType = ? AND AccountNumber IN ({placeholders}) "
                "ORDER BY MonthStart"
            )
            rows.extend(self._fetch_rows(query, [customer_type_code, *batch], query_session=query_session))
        return rows

    def fetch_call_record_rows(
        self,
        account_numbers: list[str],
        customer_segment: str,
        query_session: DashboardSqlQuerySession | None = None,
    ) -> list[tuple[Any, ...]]:
        sanitized_accounts = self._sanitize_string_keys(account_numbers)
        if not sanitized_accounts:
            return []

        customer_type_code = "COM" if customer_segment == "com" else "RES"
        rows: list[tuple[Any, ...]] = []
        max_accounts_per_batch = 900
        for start_index in range(0, len(sanitized_accounts), max_accounts_per_batch):
            batch = sanitized_accounts[start_index:start_index + max_accounts_per_batch]
            placeholders = ", ".join("?" for _ in batch)
            query = (
                "SELECT CustomerAccount, SubscriberAccount, CustomerType, MonthStart, NumberOfCalls, TotalDurationMinutes, AvgDurationMinutes, ClientSentiment, IsResolved "
                f"FROM {self._qualified_table(self.call_records_table)} "
                f"WHERE CustomerType = ? AND SubscriberAccount IN ({placeholders}) "
                "UNION "
                "SELECT CustomerAccount, SubscriberAccount, CustomerType, MonthStart, NumberOfCalls, TotalDurationMinutes, AvgDurationMinutes, ClientSentiment, IsResolved "
                f"FROM {self._qualified_table(self.call_records_table)} "
                f"WHERE CustomerType = ? AND CustomerAccount IN ({placeholders}) "
                "ORDER BY MonthStart DESC, NumberOfCalls DESC"
            )
            rows.extend(self._fetch_rows(query, [customer_type_code, *batch, customer_type_code, *batch], query_session=query_session))
        return rows

    def count_call_record_rows(
        self,
        account_numbers: list[str],
        customer_segment: str,
        query_session: DashboardSqlQuerySession | None = None,
    ) -> int:
        sanitized_accounts = self._sanitize_string_keys(account_numbers)
        if not sanitized_accounts:
            return 0

        # Run count batches (same 900-account limit) and sum across batches.
        customer_type_code = "COM" if customer_segment == "com" else "RES"
        total = 0
        max_accounts_per_batch = 900
        for start_index in range(0, len(sanitized_accounts), max_accounts_per_batch):
            batch = sanitized_accounts[start_index:start_index + max_accounts_per_batch]
            placeholders = ", ".join("?" for _ in batch)
            query = (
                "SELECT COUNT_BIG(1) "
                f"FROM {self._qualified_table(self.call_records_table)} "
                f"WHERE CustomerType = ? AND (SubscriberAccount IN ({placeholders}) OR CustomerAccount IN ({placeholders}))"
            )
            rows = self._fetch_rows(query, [customer_type_code, *batch, *batch], query_session=query_session)
            if rows and rows[0]:
                try:
                    total += int(rows[0][0] or 0)
                except (TypeError, ValueError):
                    pass
        return total

    def fetch_call_record_page_rows(
        self,
        account_numbers: list[str],
        customer_segment: str,
        page: int,
        page_size: int,
        query_session: DashboardSqlQuerySession | None = None,
    ) -> list[tuple[Any, ...]]:
        sanitized_accounts = self._sanitize_string_keys(account_numbers)
        if not sanitized_accounts:
            return []

        safe_page = max(int(page or 1), 1)
        safe_page_size = max(int(page_size or 100), 1)
        offset_rows = (safe_page - 1) * safe_page_size
        customer_type_code = "COM" if customer_segment == "com" else "RES"
        # Collect all matching rows across batches then slice for the requested page.
        all_rows: list[tuple[Any, ...]] = []
        max_accounts_per_batch = 900
        for start_index in range(0, len(sanitized_accounts), max_accounts_per_batch):
            batch = sanitized_accounts[start_index:start_index + max_accounts_per_batch]
            placeholders = ", ".join("?" for _ in batch)
            query = (
                "SELECT CustomerAccount, SubscriberAccount, CustomerType, MonthStart, NumberOfCalls, TotalDurationMinutes, AvgDurationMinutes, ClientSentiment, IsResolved "
                f"FROM {self._qualified_table(self.call_records_table)} "
                f"WHERE CustomerType = ? AND SubscriberAccount IN ({placeholders}) "
                "UNION "
                "SELECT CustomerAccount, SubscriberAccount, CustomerType, MonthStart, NumberOfCalls, TotalDurationMinutes, AvgDurationMinutes, ClientSentiment, IsResolved "
                f"FROM {self._qualified_table(self.call_records_table)} "
                f"WHERE CustomerType = ? AND CustomerAccount IN ({placeholders}) "
                "ORDER BY MonthStart DESC, NumberOfCalls DESC"
            )
            all_rows.extend(self._fetch_rows(query, [customer_type_code, *batch, customer_type_code, *batch], query_session=query_session))
        # Global sort across batches then apply page slice.
        all_rows.sort(key=lambda r: (r[3] or "", -(r[4] or 0)), reverse=True)
        return all_rows[offset_rows: offset_rows + safe_page_size]

    def fetch_account_mac_map(
        self,
        account_numbers: list[str] | None = None,
        query_session: DashboardSqlQuerySession | None = None,
    ) -> dict[str, str]:
        sanitized_accounts = self._sanitize_string_keys(account_numbers or [])
        account_mac_map: dict[str, str] = {}
        if sanitized_accounts:
            for batch in self._iter_batches(sanitized_accounts):
                placeholders = ", ".join("?" for _ in batch)
                query = (
                    "SELECT AccountNumber, ModemMac "
                    f"FROM {self._qualified_table(self.account_mac_table)} "
                    f"WHERE AccountNumber IN ({placeholders})"
                )
                rows = self._fetch_rows(query, batch, query_session=query_session)
                for row in rows:
                    if len(row) < 2:
                        continue
                    account_number = self._normalize_string_value(row[0])
                    modem_mac = self.normalize_mac_key(row[1])
                    if account_number and modem_mac:
                        account_mac_map[account_number] = modem_mac
            return account_mac_map

        rows = self._fetch_rows(
            "SELECT AccountNumber, ModemMac "
            f"FROM {self._qualified_table(self.account_mac_table)}",
            query_session=query_session,
        )
        for row in rows:
            if len(row) < 2:
                continue
            account_number = self._normalize_string_value(row[0])
            modem_mac = self.normalize_mac_key(row[1])
            if account_number and modem_mac:
                account_mac_map[account_number] = modem_mac
        return account_mac_map

    def fetch_latest_modem_health(
        self,
        mac_addresses: list[str],
        query_session: DashboardSqlQuerySession | None = None,
    ) -> dict[str, dict[str, Any]]:
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

        modem_rows: dict[str, dict[str, Any]] = {}
        for batch in self._iter_batches(sanitized_macs):
            placeholders = ", ".join("?" for _ in batch)
            query = (
                "SELECT "
                "ModemMac AS mac, "
                "ModemMac AS normalized_mac_key, "
                "IP AS ip, "
                "LastSeen AS tstamp, "
                "USINT AS usint, "
                "Status AS status, "
                "State AS state, "
                "USRXLVL AS usrxlvl, "
                "USTXPWR AS ustxpwr, "
                "USRXSNR AS usrxsnr, "
                "DSRXLVL AS dsrxlvl, "
                "DSRXSNR AS dsrxsnr, "
                "DSPREFEC AS dsprefec, "
                "DSPOSTFEC AS dspostfec, "
                "DSBW AS dsbw, "
                "USBW AS usbw, "
                "FiberNode AS fibernode, "
                "CMTS AS cmts "
                f"FROM {self._qualified_table(self.modem_health_table)} "
                f"WHERE ModemMac IN ({placeholders})"
            )
            for row in self._fetch_dict_rows(query, batch, query_session=query_session):
                modem_key = self.normalize_mac_key(row.get("normalized_mac_key") or row.get("mac"))
                if modem_key:
                    modem_rows[modem_key] = row
        return modem_rows

    def _fetch_rows(
        self,
        query: str,
        params: list[Any] | None = None,
        query_session: DashboardSqlQuerySession | None = None,
    ) -> list[tuple[Any, ...]]:
        if not self.is_configured():
            raise RuntimeError("Dashboard SQL Server is not fully configured.")

        if query_session is not None:
            return query_session.fetch_rows(query, params)

        connection = None
        cursor = None
        try:
            connection = self._open_connection()
            cursor = connection.cursor()
            cursor.execute(query, *(params or []))
            return [tuple(row) for row in cursor.fetchall()]
        except ModuleNotFoundError as exc:
            raise RuntimeError("pyodbc is required for dashboard SQL Server integration.") from exc
        except Exception:
            logger.exception("Dashboard SQL query failed.")
            raise
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

    def _fetch_dict_rows(
        self,
        query: str,
        params: list[Any] | None = None,
        query_session: DashboardSqlQuerySession | None = None,
    ) -> list[dict[str, Any]]:
        if not self.is_configured():
            raise RuntimeError("Dashboard SQL Server is not fully configured.")

        if query_session is not None:
            return query_session.fetch_dict_rows(query, params)

        connection = None
        cursor = None
        try:
            connection = self._open_connection()
            cursor = connection.cursor()
            cursor.execute(query, *(params or []))
            column_names = [column[0] for column in cursor.description]
            rows = []
            for row in cursor.fetchall():
                rows.append(
                    {
                        column_name: self._serialize_value(value)
                        for column_name, value in zip(column_names, row, strict=False)
                    }
                )
            return rows
        except ModuleNotFoundError as exc:
            raise RuntimeError("pyodbc is required for dashboard SQL Server integration.") from exc
        except Exception:
            logger.exception("Dashboard SQL query failed.")
            raise
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

    def _open_connection(self):
        try:
            pyodbc = __import__("pyodbc")
            return pyodbc.connect(self._build_connection_string(), timeout=self.timeout_seconds)
        except ModuleNotFoundError as exc:
            raise RuntimeError("pyodbc is required for dashboard SQL Server integration.") from exc

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

    def _qualified_table(self, table_name: str) -> str:
        return table_name if "." in table_name else f"{self.schema}.{table_name}"

    def _iter_batches(
        self,
        items: list[str],
        parameter_multiplier: int = 1,
        fixed_params: int = 0,
    ) -> list[list[str]]:
        max_dynamic_params = SQL_SERVER_PARAMETER_LIMIT - max(fixed_params, 0)
        safe_batch_size = max(max_dynamic_params // max(parameter_multiplier, 1), 1)
        batch_size = min(max(self.batch_size, 1), safe_batch_size)
        return [items[index:index + batch_size] for index in range(0, len(items), batch_size)]

    @staticmethod
    def _sanitize_string_keys(values: list[str]) -> list[str]:
        sanitized_values = []
        seen_values = set()
        for value in values:
            normalized_value = str(value or "").strip()
            if not normalized_value or normalized_value in seen_values:
                continue
            seen_values.add(normalized_value)
            sanitized_values.append(normalized_value)
        return sanitized_values

    @staticmethod
    def normalize_mac_key(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip().replace(":", "").upper()

    @staticmethod
    def _normalize_string_value(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        if hasattr(value, "isoformat"):
            return value.isoformat(sep=" ", timespec="seconds")
        return value
